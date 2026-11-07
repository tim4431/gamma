// Tab groups side by side (workspace/TabGroups.jsx, docs/dev/tab_groups.md):
// a tab split off into a group of its own from its menu, the group left
// behind showing a view of its page, a press on that view making its group
// live with nothing on screen moving (the groups, the docked windows, the
// paper's place, the notes' rows), a press on a note landing in that note's
// editor, the docked Notes window keeping the paper's notes while the notes
// page is live, a tab dragged onto a group's edge, a group gone with its
// last tab, an empty group with the library in it, the arrangement kept
// across a reload in this browser, and a right-click or a scrollbar press
// on a view leaving its group as it is.
import { Account, launchBrowser, live } from "../harness.mjs";
import { waitForPdf } from "./pdf.mjs";

export async function tabGroupScenarios({ server, browser, makePdf, step, until, assert, assertEq, assertNoProblems, openPage }) {
  await server.manage("create-user", "tabgroups-user", "tabgroups-pw");
  const user = await new Account(server, "tabgroups-user", "tabgroups-pw").login();
  const up = await user.upload("/api/uploads", makePdf([["Split paper page one", "Second line of the paper"], ["Page two of the paper"]]),
    "split.pdf", "application/pdf");
  const paper = await user.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Split paper", source_url: up.source_url } });
  const notes = await user.api("/api/pages", { method: "POST", body: { title: "Lecture notes" } });
  await user.api("/api/blocks", { method: "POST", body: { parent_id: notes.id, content: "Lecture notes body line" } });
  // a note folded shut, its child out of sight until it is unfolded
  const folded = await user.api("/api/blocks", { method: "POST", body: { parent_id: notes.id, content: "Folded parent note" } });
  await user.api("/api/blocks", { method: "POST", body: { parent_id: folded.id, content: "Folded child note" } });
  await user.api(`/api/blocks/${folded.id}`, { method: "PUT", body: { properties: { collapsed: true } } });

  // The account's tabs, as the server holds them, and none: each step starts
  // from none, since the tabs reach the server through a debounced push and
  // the server's copy wins on load.
  const serverTabs = async () => ((await user.api("/api/prefs/open-tabs")).value || []).map((tab) => tab.title);
  const noTabs = () => user.api("/api/prefs/open-tabs", { method: "PUT", body: { value: [] } });
  const groups = (page) => page.locator(".tabGroup.framed");
  const liveGroup = (page) => page.locator(".tabGroup.framed.live");
  const readGroup = (page) => page.locator(".tabGroup.framed:not(.live)");
  // the page on screen: drawn once, in the host laid over the live group's body
  const liveView = (page) => page.locator(".tabLiveHost");
  const blockOf = (page) => new URL(page.url()).searchParams.get("block");
  const tabIn = (scope, title) => scope.locator(".tabStrip .tab", { hasText: title });
  // Both pages open as tabs, the notes on screen: the paper by its address,
  // the notes through the page palette.
  async function openBoth(ctx) {
    const page = await openPage(ctx, `${server.base}/?page=${paper.id}&ws=${user.ws}`);
    await waitForPdf(page, 1);
    await page.keyboard.press("Control+p");
    const dialog = page.getByRole("dialog", { name: "Open a page" });
    await dialog.getByRole("textbox", { name: "Search pages by title or label" }).fill("Lecture notes");
    await until(async () => (await dialog.locator('[role="option"][data-kind="page"]').count()) === 1, { what: "the notes page listed" });
    await page.keyboard.press("Enter");
    await until(() => blockOf(page) === notes.id, { what: "the notes page opens" });
    await page.locator(".sidebar .blockList", { hasText: "Lecture notes body line" }).waitFor();
    await until(async () => (await page.locator(live(".topbar .tabStrip .tab")).count()) === 2, { what: "both tabs in the topbar's strip" });
    return page;
  }
  // A tab's drag, dropped on `target` at a point of it: the browser's drag
  // events with one data store, as the strip and the drop layers see them.
  async function dragTab(page, tab, target, at) {
    const dt = await page.evaluateHandle(() => new DataTransfer());
    await tab.dispatchEvent("dragstart", { dataTransfer: dt });
    await target.waitFor();
    const box = await target.boundingBox();
    const point = { dataTransfer: dt, clientX: box.x + box.width * at.x, clientY: box.y + box.height * at.y };
    await target.dispatchEvent("dragenter", point);
    await target.dispatchEvent("dragover", point);
    await target.dispatchEvent("drop", point);
    await tab.dispatchEvent("dragend", { dataTransfer: dt }).catch(() => {});
  }

  await step("tab groups: Split right puts the paper in a group of its own, shown to read beside the notes being edited", async () => {
    await noTabs();
    const ctx = await user.context(browser);
    const page = await openBoth(ctx);
    try {
      await tabIn(page.locator(".topbar"), "Split paper").click({ button: "right" });
      await page.getByRole("menuitem", { name: "Split right" }).click();
      await until(async () => (await groups(page).count()) === 2, { what: "two groups" });
      assertEq(await page.locator(live(".topbar .tabStrip .tab")).count(), 0, "the topbar's strip is empty: each group carries its own");
      // the notes stay on screen, in the live group on the left
      assertEq(blockOf(page), notes.id, "the page on screen is unchanged");
      await liveView(page).locator(".sidebar .blockList", { hasText: "Lecture notes body line" }).waitFor();
      assertEq(await tabIn(liveGroup(page), "Lecture notes").getAttribute("aria-selected"), "true");
      // the paper on the right, read: its PDF rendered in the pane
      const pane = readGroup(page).locator(".pagePane");
      await pane.locator('[data-page="1"] .textLayer span').first().waitFor({ timeout: 30000 });
      assertEq(await tabIn(readGroup(page), "Split paper").getAttribute("aria-selected"), "true");
      const [left, right] = await Promise.all([liveGroup(page).boundingBox(), readGroup(page).boundingBox()]);
      assert(left.x + left.width <= right.x + 1, "the live group stands left of the paper's");
      // the page on screen covers its group's body exactly, and the docked windows stand outside the groups
      const [body, main] = await Promise.all([liveGroup(page).locator(".tabGroupBody").boundingBox(), liveView(page).locator(".main").boundingBox()]);
      for (const k of ["x", "y", "width", "height"]) assert(Math.abs(body[k] - main[k]) <= 1, `the page covers its group's body: ${JSON.stringify(main)} on ${JSON.stringify(body)}`);
      const [groupsBox, slot] = await Promise.all([page.locator(".tabGroups").boundingBox(), page.locator("[data-panel-id='slot-right']").boundingBox()]);
      assert(slot && groupsBox.x + groupsBox.width <= slot.x + 1, "the right dock slot stands beside the groups, not in one");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("tab groups: a press on the read view makes its group live; the notes left behind render as a read view", async () => {
    await noTabs();
    const ctx = await user.context(browser);
    const page = await openBoth(ctx);
    try {
      await tabIn(page.locator(".topbar"), "Split paper").click({ button: "right" });
      await page.getByRole("menuitem", { name: "Split right" }).click();
      const pane = readGroup(page).locator(".pagePane");
      await pane.locator('[data-page="1"] .textLayer span').first().waitFor({ timeout: 30000 });
      const box = await pane.boundingBox();
      await page.mouse.click(box.x + box.width / 2, box.y + 40);
      await until(() => blockOf(page) === paper.id, { what: "the paper opens in the workbench" });
      await liveView(page).locator('.viewerWrap [data-page="1"] .textLayer span').first().waitFor({ timeout: 30000 });
      // the sash between the groups dragged in small steps: the paper on screen follows its group's
      // body in the same frame, with no resize observer loop reported (the window's error event, App's system log)
      await page.evaluate(() => { window.__roLoops = 0; window.addEventListener("error", (e) => { if (/ResizeObserver loop/.test(e.message)) window.__roLoops++; }); });
      const sash = await page.locator(".tabGroups .sash").first().boundingBox();
      await page.mouse.move(sash.x + sash.width / 2, sash.y + sash.height / 2);
      await page.mouse.down();
      for (let i = 1; i <= 24; i++) await page.mouse.move(sash.x + sash.width / 2 - i * 2, sash.y + sash.height / 2);
      await page.mouse.up();
      await page.waitForTimeout(300);
      assertEq(await page.evaluate(() => window.__roLoops), 0, "no resize observer loop while the sash moves");
      const [body2, main2] = await Promise.all([liveGroup(page).locator(".tabGroupBody").boundingBox(), liveView(page).locator(".main").boundingBox()]);
      for (const k of ["x", "y", "width", "height"]) assert(Math.abs(body2[k] - main2[k]) <= 1, `the page follows its group's body: ${JSON.stringify(main2)} on ${JSON.stringify(body2)}`);
      const [left, right] = await Promise.all([readGroup(page).boundingBox(), liveGroup(page).boundingBox()]);
      assert(left.x < right.x, "the live group is now the right one");
      // the notes, no longer live, show in their group as the notes tree draws them
      await readGroup(page).locator(".pagePane .sidebar .blockList .blockRow", { hasText: "Lecture notes body line" }).waitFor();
      assertEq(await readGroup(page).locator(".pagePane .pageTitleRow h3").textContent(), "Lecture notes");
      // the arrangement is this browser's: a reload keeps it, the paper still on screen
      // the reload waits for the tabs to reach the account, as a reader's would take longer than the push
      await until(async () => (await serverTabs()).length === 2, { what: "both tabs on the account" });
      await page.reload();
      await until(async () => (await groups(page).count()) === 2, { what: "two groups after the reload" });
      await until(() => blockOf(page) === paper.id, { what: "the paper is the page on screen" });
      await readGroup(page).locator(".pagePane .blockRow", { hasText: "Lecture notes body line" }).waitFor();
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("tab groups: a press on a group's view moves nothing on screen; a press on a note opens its editor where it was pressed", async () => {
    await noTabs();
    const ctx = await user.context(browser);
    const page = await openBoth(ctx);
    try {
      await tabIn(page.locator(".topbar"), "Split paper").click({ button: "right" });
      await page.getByRole("menuitem", { name: "Split right" }).click();
      const pane = readGroup(page).locator(".pagePane");
      await pane.locator('[data-page="2"]').waitFor({ timeout: 30000 });
      await pane.locator('[data-page="1"] .textLayer span').first().waitFor({ timeout: 30000 });
      // the paper's view scrolled a little, as a reader leaves it
      await pane.locator(".pdfViewer").evaluate((el) => el.scrollTo({ top: 120 }));
      await page.waitForTimeout(300);
      // What is on screen, box by box: the groups, the docked windows, the paper's first page and its
      // place, the notes' row and title, wherever each is drawn.
      const look = () => page.evaluate(() => {
        const box = (el) => { if (!el) return null; const b = el.getBoundingClientRect(); return [b.x, b.y, b.width, b.height].map((v) => Math.round(v)); };
        const viewer = document.querySelector(".pdfViewer");
        return {
          groups: [...document.querySelectorAll(".tabGroup.framed")].map(box),
          windows: [...document.querySelectorAll(".dockWindow")].map((w) => [w.querySelector(".dockGrip")?.textContent || "", box(w)]),
          notesTitle: [...document.querySelectorAll(".dockWindow .pageTitleRow h3")].map((h) => h.textContent),
          paper: viewer && { top: Math.round(viewer.scrollTop), page: box(viewer.querySelector('[data-page="1"]')) },
          row: box([...document.querySelectorAll(".blockRowWrap")].find((r) => r.textContent.includes("Lecture notes body line"))),
          list: box([...document.querySelectorAll(".tabGroup .blockList, .tabLiveHost .blockList")].find((l) => l.textContent.includes("Lecture notes body line"))),
          title: box([...document.querySelectorAll(".tabGroup .pageTitleRow h3, .tabLiveHost .pageTitleRow h3")].find((h) => h.textContent === "Lecture notes")),
        };
      });
      const before = await look();
      // the same boxes, compared as data
      const same = (x, y, what) => assertEq(JSON.stringify(x), JSON.stringify(y), what);
      same(before.notesTitle, ["Split paper"], "the docked Notes window keeps the paper's notes while the notes page is live");
      const at = await pane.boundingBox();
      await page.mouse.click(at.x + at.width * 0.6, at.y + at.height * 0.5);
      await until(() => blockOf(page) === paper.id, { what: "the paper opens" });
      await liveView(page).locator('.viewerWrap [data-page="1"] .textLayer span').first().waitFor({ timeout: 30000 });
      await readGroup(page).locator(".pagePane .blockRow", { hasText: "Lecture notes body line" }).waitFor();
      await page.waitForTimeout(500);
      const after = await look();
      same(after.groups, before.groups, "the groups stand where they stood");
      same(after.windows, before.windows, "the docked windows stand where they stood");
      same(after.notesTitle, ["Split paper"], "the Notes window shows the paper's notes, now live");
      assert(Math.abs(after.paper.top - before.paper.top) <= 2, `the paper stays at its place: ${before.paper.top} then ${after.paper.top}`);
      same(after.paper.page, before.paper.page, "the paper keeps its zoom");
      same(after.row, before.row, "the notes' row stays where it was, in the notes' view now");
      same(after.list, before.list, "the notes' list fills the same room");
      same(after.title, before.title, "the notes' title too");
      // a press on the note in the view: the notes are live again, that note's editor open, nothing moved
      const row = readGroup(page).locator(".pagePane .blockRow", { hasText: "Lecture notes body line" });
      const rb = await row.boundingBox();
      await page.mouse.click(rb.x + rb.width * 0.3, rb.y + rb.height - 10);
      await until(() => blockOf(page) === notes.id, { what: "the notes open" });
      const editor = liveView(page).locator(".blockEditorCm .cm-content");
      await editor.waitFor();
      assertEq(await editor.textContent(), "Lecture notes body line", "the pressed note's editor is open");
      await page.waitForTimeout(300);
      const last = await look();
      same(last.groups, before.groups, "the groups stand where they stood");
      same(last.windows, before.windows, "the docked windows stand where they stood");
      assert(Math.abs(last.paper.top - before.paper.top) <= 2, `the paper's view stays at its place: ${before.paper.top} then ${last.paper.top}`);
      // the paper live again; in the notes' view a folded note unfolded and its child pressed: the
      // notes open unfolded as the view had them, the child's editor open and in sight
      await page.mouse.click(at.x + at.width * 0.6, at.y + at.height * 0.5);
      await until(() => blockOf(page) === paper.id, { what: "the paper opens again" });
      const view = readGroup(page).locator(".pagePane");
      await view.locator(".blockRow", { hasText: "Folded parent note" }).locator(".collapseBtn").click();
      const child = view.locator(".blockRow", { hasText: "Folded child note" });
      await child.waitFor();
      assertEq(blockOf(page), paper.id, "unfolding a note in a view leaves the paper live");
      const cb = await child.boundingBox();
      await page.mouse.click(cb.x + cb.width * 0.3, cb.y + cb.height - 10);
      await until(() => blockOf(page) === notes.id, { what: "the notes open" });
      const childEditor = liveView(page).locator(".blockEditorCm .cm-content", { hasText: "Folded child note" });
      await childEditor.waitFor();
      const eb = await childEditor.boundingBox();
      assert(Math.abs(eb.y - cb.y) < cb.height, `the child's editor opens where it was pressed: ${eb.y} for ${cb.y}`);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("tab groups: a tab dropped on a group's bottom edge stacks the groups; the last tab closed takes its group away", async () => {
    await noTabs();
    const ctx = await user.context(browser);
    const page = await openBoth(ctx);
    try {
      await tabIn(page.locator(".topbar"), "Split paper").click({ button: "right" });
      await page.getByRole("menuitem", { name: "Split right" }).click();
      await until(async () => (await groups(page).count()) === 2, { what: "two groups" });
      // while a tab is dragged, what lies under the pointer over the live group is its drop layer,
      // not the page laid over its body
      const drag = await page.evaluateHandle(() => new DataTransfer());
      await tabIn(readGroup(page), "Split paper").dispatchEvent("dragstart", { dataTransfer: drag });
      await liveGroup(page).locator(".tabDropLayer").waitFor();
      const over = await liveGroup(page).locator(".tabGroupBody").evaluate((body) => {
        const b = body.getBoundingClientRect();
        return !!document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2)?.closest(".tabDropLayer");
      });
      assert(over, "the live group's drop layer is over its page");
      await tabIn(readGroup(page), "Split paper").dispatchEvent("dragend", { dataTransfer: drag });
      await until(async () => (await page.locator(live(".tabDropLayer")).count()) === 0, { what: "the drop layers go with the drag" });
      // the notes' tab (live, on the left) onto the lower edge of the paper's group: a column of
      // paper over notes, the live role following the notes; the emptied left group goes
      const target = readGroup(page).locator(".tabDropLayer");
      await dragTab(page, tabIn(liveGroup(page), "Lecture notes"), target, { x: 0.5, y: 0.96 });
      await until(async () => {
        if ((await groups(page).count()) !== 2) return false;
        const [a, b] = await Promise.all([groups(page).nth(0).boundingBox(), groups(page).nth(1).boundingBox()]);
        return a && b && Math.abs(a.x - b.x) < 2 && a.y + a.height <= b.y + 1;
      }, { what: "the two groups stacked, one over the other" });
      assertEq(blockOf(page), notes.id, "the notes stay on screen");
      await liveView(page).locator(".sidebar .blockList", { hasText: "Lecture notes body line" }).waitFor();
      assert((await groups(page).nth(1).getAttribute("class")).includes("live"), "the live group is the lower one");
      assertEq(await page.locator(live(".tabDropLayer")).count(), 0, "the drop layers go with the drag");
      // the paper's tab closed: its group goes, and the one group's strip is the topbar's again
      await tabIn(readGroup(page), "Split paper").hover();
      await tabIn(readGroup(page), "Split paper").locator(".tabClose").click();
      await until(async () => (await groups(page).count()) === 0, { what: "one group, unframed" });
      await until(async () => (await page.locator(live(".topbar .tabStrip .tab")).count()) === 1, { what: "the topbar's strip holds the one tab" });
      assertEq(blockOf(page), notes.id);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("tab groups: the only tab split off opens an empty group with the library in it, closed from its view", async () => {
    await noTabs();
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?page=${paper.id}&ws=${user.ws}`);
    try {
      await waitForPdf(page, 1);
      await until(async () => (await page.locator(live(".topbar .tabStrip .tab")).count()) === 1, { what: "the paper's tab" });
      await tabIn(page.locator(".topbar"), "Split paper").click({ button: "right" });
      await page.getByRole("menuitem", { name: "Split right" }).click();
      await until(async () => (await groups(page).count()) === 2, { what: "two groups" });
      // the new, empty group is live and shows the library; the paper reads on the left
      await until(() => !blockOf(page), { what: "the library is on screen" });
      await liveView(page).locator(".fileRow", { hasText: "Split paper" }).waitFor();
      await readGroup(page).locator('.pagePane [data-page="1"] .textLayer span').first().waitFor({ timeout: 30000 });
      // a page picked there opens in that group
      await liveView(page).locator(".fileRow", { hasText: "Lecture notes" }).dblclick();
      await until(() => blockOf(page) === notes.id, { what: "the notes open" });
      await until(async () => (await tabIn(liveGroup(page), "Lecture notes").count()) === 1, { what: "its tab in the live group's strip" });
      // Join from a tab's menu: one group again, the notes still on screen
      await tabIn(liveGroup(page), "Lecture notes").click({ button: "right" });
      await page.getByRole("menuitem", { name: "Join tab groups" }).click();
      await until(async () => (await groups(page).count()) === 0, { what: "one group" });
      await until(async () => (await page.locator(live(".topbar .tabStrip .tab")).count()) === 2, { what: "both tabs in the topbar's strip" });
      assertEq(blockOf(page), notes.id);
      // the notes closed, the paper alone again: Split right opens an empty group, live, with the library;
      // a press on the paper's view takes the live role back, and the empty group, no longer live, closes from its own view
      await tabIn(page.locator(".topbar"), "Lecture notes").hover();
      await tabIn(page.locator(".topbar"), "Lecture notes").locator(".tabClose").click();
      // the close goes back to the library the notes were opened from, as it always did
      await until(() => !blockOf(page), { what: "the library, where the notes were opened from" });
      await tabIn(page.locator(".topbar"), "Split paper").click();
      await until(() => blockOf(page) === paper.id, { what: "the paper is the page on screen" });
      await tabIn(page.locator(".topbar"), "Split paper").click({ button: "right" });
      await page.getByRole("menuitem", { name: "Split right" }).click();
      await until(async () => (await groups(page).count()) === 2 && !blockOf(page), { what: "an empty live group" });
      const view = readGroup(page).locator(".pagePane");
      await view.locator('[data-page="1"] .textLayer span').first().waitFor({ timeout: 30000 });
      const at = await view.boundingBox();
      await page.mouse.click(at.x + at.width / 2, at.y + 30);
      await until(() => blockOf(page) === paper.id, { what: "the paper opens again" });
      await readGroup(page).locator(".pagePaneEmpty").waitFor();
      await readGroup(page).getByRole("button", { name: "Close this group" }).click();
      await until(async () => (await groups(page).count()) === 0, { what: "one group again" });
      assertEq(blockOf(page), paper.id);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("tab groups: a right-click or a press on its scrollbar leaves a read view's group as it is; a click makes it live", async () => {
    await noTabs();
    // Scrollbars that take room and presses, as Windows and Linux draw them (the suite's browser hides them).
    const shown = await launchBrowser({ scrollbars: true });
    const ctx = await user.context(shown);
    try {
      const page = await openBoth(ctx);
      await tabIn(page.locator(".topbar"), "Split paper").click({ button: "right" });
      await page.getByRole("menuitem", { name: "Split right" }).click();
      const scroller = readGroup(page).locator(".pagePane .pdfViewer");
      await readGroup(page).locator('.pagePane [data-page="1"] .textLayer span').first().waitFor({ timeout: 30000 });
      // a right-click on the paper's page: no switch, whatever the platform sends after the release
      const box = await readGroup(page).locator(".pagePane").boundingBox();
      await page.mouse.click(box.x + box.width / 2, box.y + 60, { button: "right" });
      await page.keyboard.press("Escape");
      await page.waitForTimeout(400);
      assertEq(blockOf(page), notes.id, "a right-click opens nothing");
      assertEq(await readGroup(page).locator(".pagePane").count(), 1, "the paper still reads in its pane");
      // a press on the viewer's scrollbar track: it scrolls the view, and the notes stay live
      const bar = await scroller.evaluate((el) => {
        const r = el.getBoundingClientRect();
        return { width: el.offsetWidth - el.clientLeft - el.clientWidth, x: r.left + el.clientLeft + el.clientWidth, y: r.top + r.height * 0.8,
          overflows: el.scrollHeight > el.clientHeight, top: el.scrollTop };
      });
      assert(bar.overflows && bar.width >= 4, `the read view shows a scrollbar: ${JSON.stringify(bar)}`);
      await page.mouse.click(bar.x + bar.width / 2, bar.y);
      await until(async () => (await scroller.evaluate((el) => el.scrollTop)) > bar.top, { what: "the track press scrolls the view" });
      await page.waitForTimeout(400);
      assertEq(blockOf(page), notes.id, "a scrollbar press opens nothing");
      assertEq(await liveView(page).locator(".sidebar .blockList", { hasText: "Lecture notes body line" }).count(), 1, "the notes stay live");
      // a plain click on the page makes the group live
      await page.mouse.click(box.x + box.width / 2, box.y + 60);
      await until(() => blockOf(page) === paper.id, { what: "the paper opens in the workbench" });
      assertNoProblems(page);
    } finally { await ctx.close(); await shown.close(); }
  });
}
