import { generateNKeysBetween } from "fractional-indexing";
import { Account, arrived, assertNear, fakeAiModels, live, makePdf, sleep, wanted } from "../harness.mjs";
import { selectPdfText, waitForPdf } from "./pdf.mjs";

// Motion (docs/dev/ui-design.md, "Motion"): a menu grows in from the point
// it opens from and leaves through a still copy of itself, inert and out of
// the accessibility tree, that goes once its way out has played; a closed
// dialog's copy never counts as an open dialog, so a shortcut works while
// it fades; under reduced motion nothing lingers; a segmented control's
// frame slides to the option chosen; in the notes, a block added mid-list
// fades in while the rows below glide to make room, the agent's row still
// being written among them, but nothing moves on a
// page opening or for another client's block, and a fold closes before
// its rows go; the Inbox opens a tab on what it showed last, a first
// listing waits blank in its room and comes into focus, and its rows and
// day headers leave through copies; In progress goes with the last task
// running as Finished's header glides up, and a removed task leaves; the
// PDF margin's cards grow in and shrink away, a reply glides its thread to
// the top of the Comments window (from where its row stood at any Interface
// size) and rises into its thread; the share
// popover and Recently deleted open on their content; a tab opened rises
// in and one closed sinks out as the next glides over its place; the
// library's listing glides within one listing and comes into focus when
// another takes its place, but not for a read that is a load (a metadata
// lookup's end among them); a theme changes every colour in one frame;
// under reduced motion none of it moves.
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
      // A press outside the list (the editing bar's Insert…) and text typed in
      // it land a glide at once, so a popup they open is placed at rest.
      const landsOn = (how) => page.evaluate(async (how) => {
        for (let i = 0; i < 20 && !document.querySelector("[data-moving]"); i++) await new Promise(requestAnimationFrame);
        const gliding = document.querySelectorAll("[data-moving]").length;
        if (how === "press") document.body.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
        else document.querySelector(".blockList .cm-content").dispatchEvent(new InputEvent("beforeinput", { bubbles: true, inputType: "insertText", data: "x" }));
        return [gliding, document.querySelectorAll("[data-moving]").length];
      }, how);
      const still = () => until(async () => (await page.locator("[data-moving]").count()) === 0, { what: "the glides land" });
      await still();
      await page.keyboard.press("Shift+Tab");
      assertEq((await landsOn("press")).join(), "1,0", "a press outside the list lands the outdent's glide");
      await still();
      await page.keyboard.press("Tab");
      assertEq((await landsOn("input")).join(), "1,0", "text typed in the list lands the indent's glide");
      await glides(page);
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

  await step("motion: the agent's row still being written glides with the rows around it when the person adds a block above it", async () => {
    const ghosted = await user.api("/api/pages", { method: "POST", body: { title: "Ghosted" } });
    await user.api(`/api/pages/${ghosted.id}/ops`, { method: "POST", body: { client: "e2e", ops:
      generateNKeysBetween(null, null, 4).map((position, i) => ({ op: "insert", id: `gh${i}`, parent: ghosted.id, position, content: `Ghosted ${i}` })) } });
    const ctx = await user.context(browser, { viewport: { width: 1500, height: 900 } });
    await fakeAiModels(ctx);
    await ctx.addInitScript(() => {
      localStorage.setItem("gamma-ai-login-check", "off");
      window.chatStreams = [];
      const fetch = window.fetch.bind(window);
      window.fetch = (input, init) => {
        if (!String(input).endsWith("/api/ai/chat")) return fetch(input, init);
        return Promise.resolve(new Response(new ReadableStream({ start(controller) {
          window.chatStreams.push({ push: (event) => controller.enqueue(new TextEncoder().encode(JSON.stringify(event) + "\n")) });
          init.signal.addEventListener("abort", () => controller.error(new DOMException("Stopped", "AbortError")));
        } }), { headers: { "Content-Type": "application/x-ndjson" } }));
      };
    });
    const page = await openPage(ctx, `${server.base}/?ws=${user.ws}&page=${ghosted.id}`);
    try {
      await rowOf(page, "gh3").waitFor();
      const input = page.getByRole("combobox", { name: "Message AI" });
      await input.waitFor();
      await page.waitForLoadState("networkidle");
      await input.fill("Add a note");
      await input.press("Enter");
      await page.waitForFunction(() => window.chatStreams.length === 1);
      await page.evaluate((parent_id) => window.chatStreams[0].push({ progress: { id: "t1", tool: "create_block", parent_id, after_id: "gh2", content: "The agent is writing this" } }), ghosted.id);
      const ghost = page.locator(".aiGhostWrap");
      await ghost.waitFor();
      await rowOf(page, "gh0").locator(".blockRow").click();
      await page.locator(".blockEditorCm").first().waitFor();
      await page.keyboard.press("End");
      await sleep(400);
      // where the rows below and the ghost stood, and where each is drawn as its glide starts
      await page.evaluate(() => {
        const name = (el) => el.dataset.blockId || (el.classList.contains("aiGhostWrap") ? "ghost" : "");
        const top = (el) => Math.round(el.getBoundingClientRect().top);
        const rows = ".sortableBlockWrap[data-block-id], .aiGhostWrap";
        window.__stood = Object.fromEntries([...document.querySelectorAll(rows)].map((el) => [name(el), top(el)]));
        window.__drawn = {};
        new MutationObserver((records) => {
          for (const r of records) if (r.target.matches(rows) && r.target.hasAttribute("data-moving")) window.__drawn[name(r.target)] ??= top(r.target);
        }).observe(document.querySelector(".blockList") || document.body, { subtree: true, attributes: true, attributeFilter: ["data-moving"] });
      });
      await page.keyboard.press("Shift+Enter");
      await until(async () => (await page.locator(".sortableBlockWrap[data-block-id]").count()) === 5, { what: "the new row" });
      await arrived(rowOf(page, "gh3"));
      const { stood, drawn } = await page.evaluate(() => ({ stood: window.__stood, drawn: window.__drawn }));
      for (const id of ["gh1", "gh2", "ghost", "gh3"]) {
        assert(drawn[id] !== undefined, `${id} glides: ${JSON.stringify(drawn)}`);
        assertNear(drawn[id], stood[id], 2, `${id} starts where it stood`);
      }
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

  // Every glide, arrival and leaving copy the page makes, and every CSS
  // animation that starts, from before it loads: "moving <key>", "entering
  // fade empty", "leaving row inboxRow", "leaving row inboxRow +ids" when a
  // copy kept an id, "reveal in", "anim sharpen-in reveal".
  const record = (ctx) => ctx.addInitScript(() => {
    window.__moves = [];
    const name = (el) => (typeof el.className === "string" && el.className ? el.className.split(" ")[0] : el.localName);
    const ids = ["data-motion-key", "data-inbox-id", "data-card-for", "data-thread-id", "data-comment-id"];
    const go = () => {
      new MutationObserver((records) => {
        for (const r of records) {
          if (r.type === "attributes") {
            const v = r.target.getAttribute(r.attributeName);
            if (v === null) continue;
            if (r.attributeName === "data-reveal") window.__moves.push(`reveal ${v}`);
            else if (r.attributeName === "data-entering") window.__moves.push(`entering ${v} ${r.target.dataset.motionKey || name(r.target)}`);
            else window.__moves.push(`moving ${r.target.dataset.motionKey || name(r.target)}`);
            continue;
          }
          for (const n of r.addedNodes) {
            if (n.nodeType !== 1 || !n.dataset.leaving) continue;
            const kept = ids.some((a) => n.hasAttribute(a) || n.querySelector(`[${a}]`));
            window.__moves.push(`leaving ${n.dataset.leaving} ${name(n)}${kept ? " +ids" : ""}`);
          }
        }
      }).observe(document, { subtree: true, childList: true, attributes: true, attributeFilter: ["data-moving", "data-entering", "data-reveal"] });
      document.addEventListener("animationstart", (e) => window.__moves.push(`anim ${e.animationName} ${name(e.target)}`), true);
    };
    if (document.documentElement) go(); else document.addEventListener("readystatechange", go, { once: true });
  });
  const moves = (page) => page.evaluate(() => window.__moves.splice(0));
  // The requests `pattern` matches, answered `ms` later.
  const slowly = (page, pattern, ms) => page.route(pattern, async (route) => { await sleep(ms); await route.continue().catch(() => {}); });
  const copiesGone = (page) => until(async () => (await page.locator("[data-leaving]").count()) === 0, { timeout: 3000, what: "every copy goes" });

  // motion-ann shares pages with motion-user: an Inbox item each.
  await server.manage("create-user", "motion-ann", "motion-ann-pw");
  const ann = await new Account(server, "motion-ann", "motion-ann-pw").login();
  const shareWithUser = async (title) => {
    const shared = await ann.api("/api/pages", { method: "POST", body: { title } });
    await ann.api(`/api/share/${shared.id}`, { method: "POST", body: { audience: "list", users: [{ name: "motion-user", role: "view" }] } });
  };
  const bell = (page) => page.locator('[data-guide="header.inbox"]');
  const inbox = (page) => page.locator(live(".inboxPopover"));
  const inboxRows = (page) => inbox(page).locator(live(".inboxRow"));

  await step("motion: the Inbox opens a tab on what it showed last; a tab's first listing waits blank in its room and comes into focus; a row read in Unread leaves through a keyless copy as the rows below close up, a day's header goes with its last row as the empty state comes in, and Mark all read empties the tab at once", async () => {
    for (const title of ["Shared one", "Shared two", "Shared three"]) await shareWithUser(title);
    const ctx = await user.context(browser);
    await record(ctx);
    const page = await openPage(ctx, home);
    try {
      await bell(page).click();
      await until(async () => (await inboxRows(page).count()) === 3, { what: "the three shares" });
      await page.keyboard.press("Escape");
      await inbox(page).waitFor({ state: "detached" });
      // opened again, with its listing slow: the rows it showed, at once
      await slowly(page, "**/api/inbox?filter=unread*", 800);
      await bell(page).click();
      await inbox(page).waitFor();
      assertEq(await inboxRows(page).count(), 3, "the rows it showed, while it looks again");
      assertEq(await inbox(page).locator(".inboxLoading").count(), 0, "no Loading…");
      await page.unroute("**/api/inbox?filter=unread*");

      // All, the first time, slow: blank in the room the listing had, Loading… after a moment, then the rows come into focus
      const box = inbox(page).locator(".inboxRows");
      const tall = await box.evaluate((el) => el.offsetHeight);
      await slowly(page, "**/api/inbox?filter=all*", 900);
      await moves(page);
      await inbox(page).locator(".segGroup button", { hasText: "All" }).click();
      const waiting = await box.evaluate((el) => ({ h: el.offsetHeight, reveal: el.dataset.reveal, shown: getComputedStyle(el.querySelector(".inboxLoading")).opacity }));
      assertEq(waiting.reveal, "wait", "All waits for its first listing");
      assertNear(waiting.h, tall, 1, "in the room the listing had");
      assertEq(waiting.shown, "0", "Loading… not shown yet");
      await until(async () => (await box.evaluate((el) => getComputedStyle(el.querySelector(".inboxLoading") || el).opacity)) === "1", { what: "Loading… fades in after a moment" });
      await until(async () => (await inboxRows(page).count()) === 3, { what: "All's rows" });
      // its animation starts on the frame after the rows land; if it never
      // does, the assert below lists what came instead
      await page.waitForFunction(() => window.__moves.includes("anim sharpen-in reveal"), null, { timeout: 2000 }).catch(() => {});
      let seen = await moves(page);
      assert(seen.includes("reveal in") && seen.includes("anim sharpen-in reveal"), `the listing comes into focus: ${seen}`);
      assert(!seen.some((m) => m.startsWith("entering ") || m.startsWith("moving ")), `nothing else moves for a tab's listing: ${seen}`);
      await page.unroute("**/api/inbox?filter=all*");
      // back to Unread: what it showed, at once
      await inbox(page).locator(".segGroup button", { hasText: "Unread" }).click();
      assertEq(await inboxRows(page).count(), 3, "Unread as it was");
      await sleep(400);

      await moves(page);
      const first = inboxRows(page).first();
      await first.hover();
      await first.getByRole("button", { name: "Mark read" }).click();
      await until(async () => (await inboxRows(page).count()) === 2, { what: "the row leaves Unread" });
      seen = await moves(page);
      assert(seen.includes("leaving row inboxRow"), `it leaves through a copy: ${seen}`);
      assert(!seen.some((m) => m.endsWith("+ids")), `the copy carries no key or id: ${seen}`);
      assert(seen.filter((m) => m.startsWith("moving ")).length >= 1, `the rows below close up: ${seen}`);
      await arrived(inboxRows(page).first());
      await copiesGone(page);
      for (let n = 2; n > 0; n--) {
        const row = inboxRows(page).first();
        await row.hover();
        await row.getByRole("button", { name: "Mark read" }).click();
        await until(async () => (await inboxRows(page).count()) === n - 1, { what: "the next row leaves" });
        await copiesGone(page);
      }
      seen = await moves(page);
      assert(seen.includes("leaving row popoverSection"), `the day's header goes with its last row: ${seen}`);
      assert(seen.includes("entering fade empty"), `the empty state comes in: ${seen}`);
      await inbox(page).locator(".setEmpty", { hasText: "You're all caught up." }).waitFor();

      // two more, then Mark all read: at once
      await shareWithUser("Shared four");
      await shareWithUser("Shared five");
      await until(async () => (await inboxRows(page).count()) === 2, { timeout: 8000, what: "the listing's next look brings them" });
      await arrived(inboxRows(page).first());
      await moves(page);
      await inbox(page).getByRole("button", { name: "Mark all read", exact: true }).click();
      await inbox(page).locator(".setEmpty", { hasText: "You're all caught up." }).waitFor();
      seen = await moves(page);
      assert(!seen.some((m) => m.startsWith("leaving row")), `Mark all read takes them at once: ${seen}`);
      await page.keyboard.press("Escape");
      await assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("motion: in Background tasks the last task running finishes and In progress goes with it as Finished's header glides up, one removed leaves through a copy, and Clear finished takes the rest at once", async () => {
    const ctx = await user.context(browser);
    await record(ctx);
    const page = await openPage(ctx, home);
    try {
      await page.waitForSelector(".folderNewBtn", { timeout: 15000 });
      const upload = async (files) => {
        await page.click("button[aria-label='Add']");
        const chooser = page.waitForEvent("filechooser");
        await page.locator(".addPopover .ctxMenuItem", { hasText: "Upload files" }).click();
        await (await chooser).setFiles(files);
      };
      await upload([1, 2].map((i) => ({ name: `quick-${i}.pdf`, mimeType: "application/pdf", buffer: Buffer.from(makePdf([[`Quick ${i}`]])) })));
      const tray = page.locator(live(".tasksPopover"));
      const row = (text) => tray.locator(live(".taskRow"), { hasText: text });
      // (what the quick ones start, indexing and the rest, over before the slow one)
      await page.click("button[aria-label='Background tasks']");
      await row("quick-2.pdf").waitFor();
      await sleep(2000); // (a poll's turn: the jobs the uploads start show)
      await until(async () => !(await tray.locator(live(".popoverSection"), { hasText: "In progress" }).count()), { timeout: 15000, what: "nothing running" });
      await page.keyboard.press("Escape");
      await tray.waitFor({ state: "detached" });
      // a slow upload, the tray open on it: it runs, then finishes
      const cdp = await ctx.newCDPSession(page);
      await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 10, downloadThroughput: -1, uploadThroughput: (768 * 1024) / 8 });
      await upload([{ name: "slow.pdf", mimeType: "application/pdf", buffer: Buffer.from(makePdf([["Slow"]], { padBytes: 1024 * 1024 })) }]);
      await page.click("button[aria-label='Background tasks']");
      await row("slow.pdf").locator(".taskBar").waitFor({ timeout: 10000 });
      await row("quick-2.pdf").waitFor();
      await moves(page);
      await until(async () => (await row("slow.pdf").getAttribute("class")).includes("done"), { timeout: 20000, what: "the slow upload finishes" });
      await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
      let seen = await moves(page);
      assert(seen.some((m) => m === "moving head:finished"), `Finished's header moves up: ${seen}`);
      assert(seen.includes("leaving row popoverSection"), `In progress goes with its last row: ${seen}`);
      await copiesGone(page);
      await arrived(row("quick-2.pdf"));

      await moves(page);
      const quick = row("quick-2.pdf");
      await quick.hover();
      await quick.getByRole("button", { name: "Remove quick-2.pdf", exact: true }).click();
      await quick.waitFor({ state: "detached" });
      seen = await moves(page);
      assert(seen.includes("leaving row taskRow"), `it leaves through a copy: ${seen}`);
      await copiesGone(page);

      // (the slow upload's metadata lookup over first, as the quick ones' were:
      // a row still running when the clear lands leaves with the next listing)
      await until(async () => !(await tray.locator(live(".popoverSection"), { hasText: "In progress" }).count()), { timeout: 15000, what: "nothing running" });
      await copiesGone(page);
      await moves(page);
      await tray.getByRole("button", { name: "Clear finished", exact: true }).click();
      await tray.locator(".setEmpty", { hasText: "Nothing running" }).waitFor();
      await sleep(300);
      seen = await moves(page);
      assert(!seen.some((m) => m.startsWith("leaving row")), `Clear finished takes them at once: ${seen}`);
      await page.keyboard.press("Escape");
      await assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // A paper of four lines, a highlight on each (made through the page's
  // selection popup), for the margin's cards and the Comments window.
  const four = makePdf([["A line about alpha", "", "", "", "A line about beta", "", "", "", "A line about gamma", "", "", "", "A line about delta"]]);
  const up = await user.upload("/api/uploads", four, "four.pdf", "application/pdf");
  const paper = await user.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Four lines", source_url: up.source_url } });
  const paperUrl = `${server.base}/?ws=${user.ws}&page=${paper.id}`;
  const hl = {};
  const post = (block_id, body, thread_id) => until(async () => (await user.api(`/api/pages/${paper.id}/comments`, { method: "POST", body: { block_id, body, thread_id } }).catch(() => null))?.thread, { what: `"${body}" stored` });
  // with room beside the page: its cards in the margin
  const withMargin = async (page) => {
    await waitForPdf(page, 1);
    const zoomOut = page.getByRole("button", { name: "Zoom out", exact: true });
    for (let i = 0; i < 12 && !(await page.locator('[data-page="1"] .commentCard').count()); i++) { await zoomOut.click(); await sleep(250); }
    await page.locator('[data-page="1"] .commentCard').first().waitFor({ timeout: 5000 });
  };
  const card = (page, text) => page.locator(live(".commentCard"), { hasText: text });

  await step("motion: a card grows into the PDF margin and one resolved shrinks away; a reply glides its thread to the top of the Comments window, and one typed in the card rises into its thread; a new comment's card hands its place to its thread's, still", async () => {
    const ctx = await user.context(browser, { viewport: { width: 1600, height: 900 } });
    await record(ctx);
    const page = await openPage(ctx, paperUrl);
    try {
      await waitForPdf(page, 1);
      for (const word of ["alpha", "beta", "gamma", "delta"]) {
        const before = await page.evaluate(() => [...new Set([...document.querySelectorAll('[data-page="1"] [data-hl-id]')].map((el) => el.dataset.hlId))]);
        await selectPdfText(page, 1, `about ${word}`);
        await page.waitForSelector(".plainTip .colorBtn", { timeout: 5000 });
        await page.locator(".plainTip .colorBtn").first().click();
        hl[word] = await until(async () => (await page.evaluate(() => [...new Set([...document.querySelectorAll('[data-page="1"] [data-hl-id]')].map((el) => el.dataset.hlId))]))
          .find((id) => !before.includes(id)), { what: `the highlight on ${word}` });
        await page.keyboard.press("Escape");
      }
      const alpha = await post(hl.alpha, "On alpha");
      await post(hl.beta, "On beta");
      await withMargin(page);
      await card(page, "On beta").waitFor();
      await page.getByRole("button", { name: "View", exact: true }).click();
      await page.locator(".menuPopover .ctxMenuItem", { hasText: "Comments" }).click();
      await page.keyboard.press("Escape");
      const panelRows = page.locator(".commentsPanel .commentsPanelRow");
      await until(async () => (await panelRows.count()) === 2, { what: "the window's two threads" });
      await sleep(400);
      await moves(page);

      // a reply to alpha (the lower row): it glides to the top
      await post(hl.alpha, "A reply on alpha", alpha.id);
      await until(async () => (await panelRows.first().innerText()).includes("On alpha"), { timeout: 8000, what: "alpha's thread on top" });
      let seen = await moves(page);
      assert(seen.includes(`moving ${alpha.id}`), `the replied thread glides up: ${seen}`);
      // a new thread: its card grows in, its row rises in
      await post(hl.gamma, "On gamma");
      await card(page, "On gamma").waitFor({ timeout: 8000 });
      await until(async () => (await panelRows.count()) === 3, { what: "gamma's row" });
      seen = await moves(page);
      assert(seen.some((m) => /^entering card \S+$/.test(m)), `its card grows in: ${seen}`);
      assert(seen.some((m) => /^entering rise \S+$/.test(m)), `its row rises in: ${seen}`);
      await arrived(card(page, "On gamma"));
      // alpha resolved: its card shrinks away, keyless
      await moves(page);
      await user.api(`/api/comments/${alpha.id}/resolve`, { method: "POST", body: { resolved: true } });
      await card(page, "On alpha").waitFor({ state: "detached", timeout: 8000 });
      seen = await moves(page);
      assert(seen.includes("leaving card commentCard"), `its card shrinks away: ${seen}`);
      await copiesGone(page);

      // the highlight menu's Comment on delta: a card of its own grows in; stored, the thread's card takes its place still
      await page.locator(`[data-page="1"] [data-hl-id="${hl.delta}"]`).first().click({ button: "right" });
      await page.locator(".ctxMenu").getByRole("menuitem", { name: "Comment", exact: true }).click();
      await page.locator(".commentCard.active .commentNew").waitFor();
      seen = await moves(page);
      assert(seen.some((m) => m === `entering card new:${hl.delta}`), `the new comment's card grows in: ${seen}`);
      await copiesGone(page);
      await sleep(300);
      await moves(page);
      await page.keyboard.type("On delta");
      // every card's top, after each change in the margin and each frame, through the hand-over
      await page.evaluate(() => {
        const margin = document.querySelector('[data-page="1"] .commentMargin');
        const tops = () => [...margin.querySelectorAll(".commentCard:not([data-leaving])")]
          .map((el) => ({ for: el.dataset.cardFor, top: Math.round(el.getBoundingClientRect().top), text: el.innerText }));
        const handoff = (window.__handoff = { before: tops(), seen: [], done: false });
        const look = () => handoff.seen.push(tops());
        new MutationObserver(look).observe(margin, { subtree: true, childList: true, attributes: true });
        const tick = () => { if (handoff.done) return; look(); requestAnimationFrame(tick); };
        requestAnimationFrame(tick);
      });
      await page.keyboard.press("Shift+Enter");
      await page.locator(".commentCard.active .commentField").waitFor();
      await until(async () => !(await page.locator(".commentCard .commentNew").count()), { what: "the new card handed over" });
      seen = await moves(page);
      assert(!seen.some((m) => /^(entering|leaving) card/.test(m)), `neither card comes in nor goes: ${seen}`);
      await sleep(500); // longer than a card's move
      const handed = await page.evaluate(() => { window.__handoff.done = true; return window.__handoff; });
      const stood = new Map(handed.before.map((c) => [c.for, c.top]));
      for (const cards of handed.seen) {
        const own = cards.filter((c) => c.for === hl.delta);
        assertEq(own.length, 1, "the highlight has one card throughout");
        assert(own[0].text.includes("On delta"), `the comment shows throughout, in the field, on its way and stored: ${JSON.stringify(own[0].text)}`);
        for (const c of cards) assertNear(c.top, stood.get(c.for), 1.5, `the card for ${c.for === hl.delta ? "delta" : c.for} stays where it stood`);
      }
      // a reply typed in the active card rises into its thread
      await sleep(300);
      await page.keyboard.type("A reply on delta");
      await page.keyboard.press("Shift+Enter");
      await page.locator(".commentCard.active .commentBody", { hasText: "A reply on delta" }).waitFor();
      seen = await moves(page);
      assert(seen.some((m) => m.startsWith("entering rise ")), `the reply rises in: ${seen}`);
      await page.keyboard.press("Escape");
      await assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("motion: at an Interface size of 130% the Comments window's rows, zoomed buttons, start their glides from where they stood", async () => {
    const note = (block_id, body, thread_id) => until(async () => (await user.api(`/api/pages/${pg.id}/comments`, { method: "POST", body: { block_id, body, thread_id } }).catch(() => null))?.thread, { what: `"${body}" stored` });
    const older = await note("mv0", "Zoomed older");
    await note("mv1", "Zoomed newer");
    const ctx = await user.context(browser);
    await ctx.addInitScript(() => { try { localStorage.setItem("gamma-ui-scale", "1.3"); } catch {} });
    const page = await openPage(ctx, notes);
    try {
      await rowOf(page, "mv3").waitFor();
      await page.getByRole("button", { name: "View", exact: true }).click();
      await page.locator(".menuPopover .ctxMenuItem", { hasText: "Comments" }).click();
      await page.keyboard.press("Escape");
      const rows = page.locator(".commentsPanel .commentsPanelRow");
      await until(async () => (await rows.count()) === 2, { what: "the window's two threads" });
      assert(await rows.first().evaluate((el) => el.currentCSSZoom > 1.25), "the rows carry the Interface size");
      await sleep(400);
      // where each row stood, and where it is drawn as its glide starts
      await page.evaluate(() => {
        const top = (el) => Math.round(el.getBoundingClientRect().top);
        window.__stood = Object.fromEntries([...document.querySelectorAll(".commentsPanelRow")].map((el) => [el.dataset.threadId, top(el)]));
        window.__drawn = {};
        new MutationObserver((records) => {
          for (const r of records) if (r.target.hasAttribute("data-moving")) window.__drawn[r.target.dataset.threadId] ??= top(r.target);
        }).observe(document.querySelector(".commentsPanel"), { subtree: true, attributes: true, attributeFilter: ["data-moving"] });
      });
      await note("mv0", "A zoomed reply", older.id);
      await until(async () => (await rows.first().innerText()).includes("Zoomed older"), { timeout: 8000, what: "the replied thread on top" });
      const { stood, drawn } = await page.evaluate(() => ({ stood: window.__stood, drawn: window.__drawn }));
      assertEq(Object.keys(drawn).sort().join(), Object.keys(stood).sort().join(), "both rows glide");
      for (const id of Object.keys(stood)) assertNear(drawn[id], stood[id], 2, `thread ${id} starts where it stood`);
      await arrived(rows.first());
      await assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("motion: the share popover opened again shows its settings at once while it asks again; Recently deleted, asked for as the pointer reaches its button, lands with its pages; the Import dialog's Zotero mark fades in once loaded", async () => {
    const shared = await user.api("/api/pages", { method: "POST", body: { title: "Share again" } });
    await user.api(`/api/share/${shared.id}`, { method: "POST" });
    for (const title of ["Binned one", "Binned two"]) {
      const binned = await user.api("/api/pages", { method: "POST", body: { title } });
      await user.api(`/api/blocks/${binned.id}`, { method: "DELETE" });
    }
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?page=${shared.id}&ws=${user.ws}`);
    try {
      await page.locator(".blockList").first().waitFor();
      const pop = page.locator(live(".sharePopover"));
      await page.click("button[aria-label='Share']");
      await pop.getByRole("button", { name: "Copy link" }).waitFor();
      await page.keyboard.press("Escape");
      await pop.waitFor({ state: "detached" });
      await slowly(page, "**/api/share-settings/**", 800);
      await page.click("button[aria-label='Share']");
      await pop.waitFor();
      assertEq(await pop.getByRole("button", { name: "Copy link" }).count(), 1, "the settings it had, at once");
      assertEq(await pop.locator(".reveal[data-reveal=\"wait\"]").count(), 0, "nothing waits");
      await page.unroute("**/api/share-settings/**");
      await page.keyboard.press("Escape");

      await page.goto(home);
      const trash = page.locator('[data-guide="home.trash"]');
      await trash.waitFor();
      const asked = page.waitForResponse((r) => r.url().endsWith("/api/trash"));
      await trash.hover();
      await (await asked).finished();
      // what the dialog holds the moment it is in the page
      await page.evaluate(() => {
        window.__trash = null;
        const look = () => {
          const d = document.querySelector('.reportModal[aria-label="Recently deleted"]');
          if (!d) return false;
          window.__trash = { rows: d.querySelectorAll(".aiProvRow").length, waiting: !!d.querySelector('.reveal[data-reveal="wait"]') };
          return true;
        };
        const mo = new MutationObserver(() => { if (look()) mo.disconnect(); });
        mo.observe(document.body, { childList: true, subtree: true });
      });
      await trash.click();
      await page.getByRole("dialog", { name: "Recently deleted" }).waitFor();
      const landed = await page.evaluate(() => window.__trash);
      assertEq(JSON.stringify(landed), JSON.stringify({ rows: 2, waiting: false }), "the dialog lands with its pages");
      await page.keyboard.press("Escape");

      const view = page.locator('[data-popover="menu"] > button');
      await view.click();
      await page.getByRole("menuitem", { name: "Import…", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Import", exact: true });
      await dialog.waitFor();
      await dialog.locator('.formatAppImage[data-fade="shown"]').waitFor();
      await page.keyboard.press("Escape");
      await assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("motion: under reduced motion an Inbox row read leaves no copy and moves nothing, a first listing's Loading… shows at once, and a resolved card goes at once", async () => {
    await shareWithUser("Shared six");
    const ctx = await user.context(browser, { reducedMotion: "reduce", viewport: { width: 1600, height: 900 } });
    await record(ctx);
    const page = await openPage(ctx, home);
    try {
      await bell(page).click();
      await until(async () => (await inboxRows(page).count()) === 1, { what: "the new share" });
      await moves(page);
      const row = inboxRows(page).first();
      await row.hover();
      await row.getByRole("button", { name: "Mark read" }).click();
      await inbox(page).locator(".setEmpty", { hasText: "You're all caught up." }).waitFor();
      let seen = await moves(page);
      assert(!seen.some((m) => /^(leaving row|moving|entering)/.test(m)), `nothing moves: ${seen}`);
      await slowly(page, "**/api/inbox?filter=archived*", 900);
      await inbox(page).locator(".segGroup button", { hasText: "Archived" }).click();
      await sleep(50);
      assertEq(await inbox(page).locator(".inboxLoading").evaluate((el) => getComputedStyle(el).opacity), "1", "Loading… at once");
      await page.unroute("**/api/inbox?filter=archived*");
      await page.keyboard.press("Escape");

      await page.goto(paperUrl);
      await withMargin(page);
      const beta = (await user.api(`/api/pages/${paper.id}/comments`)).threads.find((th) => th.comments[0].body === "On beta");
      await moves(page);
      await user.api(`/api/comments/${beta.id}/resolve`, { method: "POST", body: { resolved: true } });
      await card(page, "On beta").waitFor({ state: "detached", timeout: 8000 });
      seen = await moves(page);
      assert(!seen.some((m) => m.startsWith("leaving card")), `the card goes at once: ${seen}`);
      assertEq(await page.locator("[data-leaving]").count(), 0, "no copy");
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

  // The library read again is a load: the order it brings lands at once.
  const firstItem = (page) => page.locator(live(".fileList > .listMotion > *")).first().innerText();

  await step("motion: the library read again on coming home from an edited page moves nothing, and a delete right after still glides", async () => {
    const edited = await user.api("/api/pages", { method: "POST", body: { title: "Edited later" } });
    await user.api(`/api/pages/${edited.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "ed0", parent: edited.id, position: generateNKeysBetween(null, null, 1)[0], content: "First line" },
    ] } });
    await user.api("/api/pages", { method: "POST", body: { title: "Made after" } });
    const ctx = await user.context(browser);
    await watch(ctx);
    const page = await openPage(ctx, `${server.base}/?ws=${user.ws}&page=${edited.id}`);
    try {
      await rowOf(page, "ed0").locator(".blockRow").click();
      await page.locator(".blockEditorCm").first().waitFor();
      await page.keyboard.press("End");
      await page.keyboard.type(" and more");
      await page.keyboard.press("Escape");
      await until(async () => JSON.stringify(await user.api(`/api/blocks/${edited.id}/subtree`)).includes("and more"), { what: "the edit saved" });
      await glides(page);
      // the listing it comes home to is the one it left, then the read lands
      await page.locator('[data-guide="header.home"]').click();
      await listRow(page, "Made after").waitFor();
      await until(async () => (await firstItem(page)).includes("Edited later"), { what: "the edited page on top" });
      await page.waitForTimeout(200);
      const back = await glides(page);
      assertEq(back.length, 0, `the order the read brings lands at once: ${back}`);
      await deleteRow(page, "Made after");
      await page.locator('.fileList [data-leaving="row"]', { hasText: "Made after" }).waitFor({ state: "attached" });
      const gone = await glides(page);
      assert(gone.some((g) => g.startsWith("moving ")), `the person's own delete right after still glides: ${gone}`);
      await assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("motion: the server's view history landing after the listing reorders the Recently viewed sort without a glide", async () => {
    const viewed = [];
    for (const title of ["Viewed long ago", "Viewed just now"]) viewed.push(await user.api("/api/pages", { method: "POST", body: { title } }));
    // seen on another device: the older page last
    const at = (ago) => new Date(Date.now() - ago).toISOString();
    await user.api("/api/prefs/recent-views", { method: "PUT", body: { value: [
      { id: viewed[0].id, at: at(0) }, { id: viewed[1].id, at: at(60000) },
    ] } });
    const ctx = await user.context(browser);
    await watch(ctx);
    await ctx.addInitScript(() => { try { localStorage.setItem("gamma-home-sort-map", JSON.stringify({ "": "viewed" })); } catch {} });
    // the view history a beat behind the listing, as on a slow link (a
    // first load reads it with the other preferences, in one request)
    await ctx.route(/\/api\/prefs\?keys=[^&]*recent-views/, async (route) => {
      await new Promise((done) => setTimeout(done, 400));
      await route.continue();
    });
    const page = await openPage(ctx, home);
    try {
      await listRow(page, "Viewed long ago").waitFor();
      assert((await firstItem(page)).includes("Viewed just now"), "before the history lands the sort falls back to the modified time");
      await until(async () => (await firstItem(page)).includes("Viewed long ago"), { what: "the view history's order" });
      await page.waitForTimeout(200);
      const seen = await glides(page);
      assertEq(seen.length, 0, `the history's order lands at once: ${seen}`);
      await assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("motion: a metadata lookup ending in the background brings the titles it found without a glide", async () => {
    const scans = [];
    for (const title of ["scan_0001.pdf", "scan_0002.pdf", "scan_0003.pdf"]) {
      scans.push(await user.api("/api/pages", { method: "POST", body: { title } }));
      await sleep(1100); // a modified time of its own, so the sort tells them apart
    }
    const ctx = await user.context(browser);
    await watch(ctx);
    // a lookup the server runs (started elsewhere), done once its work is in
    let phase = "running";
    const job = (state) => ({ id: "motion-metadata", kind: "metadata", state, workspace: user.ws, title: "Paper metadata lookup",
      owner: "motion-user", created_at: new Date(Date.now() - 5000).toISOString(), updated_at: new Date().toISOString(),
      finished_at: state === "done" ? new Date().toISOString() : null, progress: { done: 1, total: 1 }, params: { papers: 1 } });
    await ctx.route((url) => url.pathname === "/api/jobs", async (route) => {
      if (route.request().method() !== "GET") return route.continue();
      const res = await route.fetch();
      const body = await res.json();
      return route.fulfill({ response: res, json: { ...body, jobs: [job(phase), ...(body.jobs || [])] } });
    });
    const page = await openPage(ctx, home);
    try {
      const order = () => page.evaluate(() => [...document.querySelectorAll(".fileList .fileRow")].map((el) => el.innerText.split("\n")[0])
        .filter((text) => /scan_|found by the lookup/.test(text)));
      await listRow(page, "scan_0003.pdf").waitFor();
      await page.waitForTimeout(1600); // the tab has seen the job running
      // what the lookup does to the oldest scan: its registry title
      await user.api(`/api/blocks/${scans[0].id}`, { method: "PUT", body: { content: "A title found by the lookup" } });
      await glides(page);
      phase = "done";
      await until(async () => (await order())[0] === "A title found by the lookup", { timeout: 10000, what: "the retitled scan on top" });
      await page.waitForTimeout(200);
      const seen = await glides(page);
      assertEq(seen.length, 0, `the order the lookup brings lands at once: ${seen}`);
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

  await step("motion: a theme change draws every colour in one frame: nothing eases from the old theme's colours, and hover eases again after", async () => {
    // The account's theme is System, so the OS turning dark changes it
    // with no pointer near anything that eases on hover.
    const ctx = await user.context(browser, { colorScheme: "light" });
    await ctx.addInitScript(() => {
      window.__eases = [];
      const name = (el) => (typeof el.className === "string" && el.className ? el.className.split(" ")[0] : el.localName);
      document.addEventListener("transitionrun", (e) => window.__eases.push(`${e.propertyName} ${name(e.target)}`), true);
    });
    const page = await openPage(ctx, home);
    try {
      await page.locator(".fileRow").first().waitFor();
      await page.waitForTimeout(500);
      assertEq(await page.locator("html").getAttribute("data-theme"), "light", "the OS's light theme");
      await page.evaluate(() => { window.__eases = []; });
      await page.emulateMedia({ colorScheme: "dark" });
      await until(() => page.locator("html").getAttribute("data-theme").then((v) => v === "dark"), { what: "the dark theme" });
      await page.waitForTimeout(400);
      assertEq((await page.evaluate(() => window.__eases)).join(", "), "", "no colour eases from the old theme");
      assertEq(await page.evaluate(() => document.documentElement.classList.contains("themeSwitch")), false, "the frame's class is gone");
      await page.locator('[data-guide="header.account"]').hover();
      await until(async () => (await page.evaluate(() => window.__eases.length)) > 0, { timeout: 2000, what: "a hover eases again" });
      await assertNoProblems(page);
    } finally { await ctx.close(); }
  });
}
