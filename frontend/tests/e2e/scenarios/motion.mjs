import { Account, arrived, assertNear, live, wanted } from "../harness.mjs";

// Motion (docs/dev/ui-design.md, "Motion"): a menu grows in from the point
// it opens from and leaves through a still copy of itself, inert and out of
// the accessibility tree, that goes once its way out has played; a closed
// dialog's copy never counts as an open dialog, so a shortcut works while
// it fades; under reduced motion nothing lingers; a segmented control's
// frame slides to the option chosen.
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
}
