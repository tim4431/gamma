// The installed web app (docs/dev/ipad.md): the manifest and its icons, the
// home-screen head tags, theme-color following the theme, the
// standalone-mode stylesheet, and the layout by orientation (upright: the
// compact shell; landscape: the docks). Chromium in a tablet-sized touch
// context — emulation, not an iPad.
export async function ipadScenarios({ server, browser, alice, makePdf, step, until, assert, assertEq, assertNoProblems, openPage }) {
  await step("ipad: install manifest, icons, status-bar colour and standalone mode", async () => {
    await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Status bar page" } });
    await alice.api("/api/prefs/open-tabs", { method: "PUT", body: { value: [] } }); // groups before this one leave tabs
    const ctx = await alice.context(browser, { hasTouch: true, deviceScaleFactor: 2, viewport: { width: 834, height: 1194 } });
    try {
      const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
      await page.waitForSelector(".folderNewBtn", { timeout: 15000 });

      const head = await page.evaluate(() => ({
        manifest: document.querySelector('link[rel="manifest"]')?.href,
        touchIcon: document.querySelector('link[rel="apple-touch-icon"]')?.href,
        capable: document.querySelector('meta[name="apple-mobile-web-app-capable"]')?.content,
        statusBar: document.querySelector('meta[name="apple-mobile-web-app-status-bar-style"]')?.content,
      }));
      assertEq(head.capable, "yes");
      assertEq(head.statusBar, "default");

      const r = await ctx.request.get(head.manifest);
      assertEq(r.status(), 200);
      assert((r.headers()["content-type"] || "").startsWith("application/manifest+json"), `manifest media type: ${r.headers()["content-type"]}`);
      const manifest = await r.json();
      assertEq(manifest.display, "standalone");
      assert(manifest.icons.some((i) => i.purpose === "maskable"), "a maskable icon");
      for (const src of [...manifest.icons.map((i) => i.src), head.touchIcon]) {
        const icon = await ctx.request.get(new URL(src, server.base).href);
        assert(icon.status() === 200 && icon.headers()["content-type"] === "image/png", `icon ${src}: ${icon.status()} ${icon.headers()["content-type"]}`);
        assertEq((await icon.body()).subarray(0, 8).toString("hex"), "89504e470d0a1a0a", `${src} is a PNG`);
      }

      // theme-color is the paint under the status bar, for the current theme
      // and after a change: upright, the library has no tab open, so the
      // compact layout drops the topbar and the library itself is on top.
      // Every colour is compared as one canvas pixel: a derived token
      // computes to an oklab() or color() string, the meta holds a hex.
      const themeColor = () => page.evaluate(() => {
        const ctx = document.createElement("canvas").getContext("2d");
        const px = (color) => { ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = color; ctx.fillRect(0, 0, 1, 1); return [...ctx.getImageData(0, 0, 1, 1).data].join(","); };
        let el = document.elementFromPoint(innerWidth / 2, 1);
        while (el && /rgba\(0, 0, 0, 0\)|transparent/.test(getComputedStyle(el).backgroundColor)) el = el.parentElement;
        const bar = document.querySelector(".app > .topbar");
        return {
          meta: px(document.querySelector('meta[name="theme-color"]').content),
          topbar: el && px(getComputedStyle(el).backgroundColor),
          bar: bar && px(getComputedStyle(bar).backgroundColor),
        };
      });
      assertEq(await page.locator(".app > .topbar").isVisible(), false, "no topbar without tabs");
      let colors = await themeColor();
      assertEq(colors.meta, colors.topbar, "theme-color matches the library under it");
      await page.getByRole("button", { name: "More", exact: true }).click();
      await page.getByRole("button", { name: "Settings…", exact: true }).click();
      await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor();
      const before = await page.locator("html").getAttribute("data-theme");
      const next = before === "sepia" ? "Gamma Dark" : "Sepia";
      await page.getByRole("button", { name: next, exact: true }).click();
      await until(() => page.locator("html").getAttribute("data-theme").then((v) => v !== before));
      await page.keyboard.press("Escape");
      await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor({ state: "detached" });
      // Backgrounds transition over 150 ms; the meta switches at once, so
      // wait for the paint to settle on it.
      await until(async () => {
        const c = await themeColor();
        return c.topbar !== colors.topbar && c.meta === c.topbar;
      }, { what: "theme-color follows the theme and the library settles on it" });
      // A tab brings the topbar back, and the status bar takes its colour.
      await page.locator(".pageCard, .fileRow", { hasText: "Status bar page" }).first().tap();
      await page.locator(".app > .topbar").waitFor();
      await until(async () => {
        const c = await themeColor();
        return c.meta === c.topbar && c.topbar === c.bar;
      }, { what: "theme-color follows the topbar once a tab is open" });

      // Installed: the document does not rubber-band and the bottom edge keeps
      // clear of the home indicator. Chromium cannot emulate display-mode
      // (CDP accepts the feature, matchMedia ignores it), so this reads the
      // bundled stylesheet's standalone block.
      const standalone = await page.evaluate(() => {
        const out = [];
        for (const sheet of document.styleSheets) {
          let rules; try { rules = sheet.cssRules; } catch { continue; }
          for (const rule of rules) {
            if (rule instanceof CSSMediaRule && /display-mode:\s*standalone/.test(rule.conditionText)) {
              for (const inner of rule.cssRules) out.push(`${inner.selectorText} { ${inner.style.cssText} }`);
            }
          }
        }
        return out;
      });
      assert(standalone.some((r) => /^html,\s*body/.test(r) && /overscroll-behavior:\s*none/.test(r)), `document overscroll rule: ${standalone.join(" | ")}`);
      assert(standalone.some((r) => /^\.app\b/.test(r) && /safe-area-inset-bottom/.test(r)), `home indicator inset: ${standalone.join(" | ")}`);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("ipad: a tap opens a library folder and page; a mouse click only selects", async () => {
    const paper = await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Tap to open paper" } });
    await alice.api(`/api/blocks/${paper.id}`, { method: "PUT", body: { properties: { folder: "tapfolder" } } });
    const ctx = await alice.context(browser, { hasTouch: true, viewport: { width: 834, height: 1194 } });
    try {
      const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
      const folder = page.locator(".pageCard, .folderRow", { hasText: "tapfolder" }).first();
      await folder.click();
      await until(() => folder.evaluate((el) => el.classList.contains("selected")), { what: "a mouse click selects the folder" });
      assert(!page.url().includes("folder="), `a mouse click stays on the library: ${page.url()}`);
      await folder.tap();
      await until(() => page.url().includes("folder=tapfolder"), { what: "a tap opens the folder" });
      await page.locator(".pageCard, .fileRow", { hasText: "Tap to open paper" }).first().tap();
      await until(() => page.url().includes(`block=${paper.id}`), { what: "a tap opens the page" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("ipad: upright is the compact shell with full-screen panels; landscape brings the docks back", async () => {
    const up = await alice.upload("/api/uploads", makePdf([["An upright tablet reads full width."]]), "upright.pdf", "application/pdf");
    const paper = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Upright paper", source_url: up.source_url } });
    const ctx = await alice.context(browser, { hasTouch: true, viewport: { width: 834, height: 1194 } });
    try {
      const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}&block=${paper.id}`);
      await page.waitForSelector(".app.phoneUI .phoneBottomBar");
      await page.waitForSelector('[data-page="1"] .textLayer span', { timeout: 20000 });
      assertEq(await page.locator(".dockWindow").count(), 0, "no docked windows while upright");
      const viewer = await page.locator(".viewerWrap").boundingBox();
      assert(viewer.width > 800, `the PDF takes the full width: ${viewer.width}`);
      await page.getByRole("button", { name: "Notes", exact: true }).tap();
      const notes = page.locator(".phonePanel:not(.phonePanelHidden) .dockWindow");
      await notes.waitFor();
      assert((await notes.boundingBox()).width > 800, "Notes open full screen");
      await page.setViewportSize({ width: 1194, height: 834 });
      await until(async () => (await page.locator(".app.phoneUI").count()) === 0, { what: "landscape leaves the compact shell" });
      await page.locator(".dockSlot .dockWindow").first().waitFor();
      assertEq(await page.locator(".phoneBottomBar").count(), 0, "no bottom bar with the docks");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("phone: a labelled bottom bar, the More sheet, no empty topbar, the Library tab as home", async () => {
    await alice.api("/api/prefs/open-tabs", { method: "PUT", body: { value: [] } });
    const note = await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "A phone title long enough to need a second line on a narrow screen" } });
    await alice.api(`/api/blocks/${note.id}`, { method: "PUT", body: { properties: { category: "phonelabel" } } });
    const ctx = await alice.context(browser, { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    try {
      const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
      await page.waitForSelector(".app.phoneUI .folderNewBtn");
      const bar = page.locator(".phoneBottomBar");
      const barWords = async () => (await bar.innerText()).split(/\s+/).filter(Boolean).join(" ");
      // The library: no topbar (the Library tab is home), every bar item
      // labelled, the listing bar and rows inside the screen.
      assertEq(await page.locator(".app > .topbar").isVisible(), false, "no topbar without tabs");
      assertEq(await page.locator('[data-guide="header.home"]').count(), 1, "one home anchor: the Library tab");
      assertEq(await barWords(), "Library Chat Add Search More");
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "nothing past the right edge");
      for (const box of await page.locator(".homeListBar > *").evaluateAll((els) => els.map((el) => el.getBoundingClientRect().toJSON()))) {
        assert(box.right <= 390, `listing bar control inside the screen: ${JSON.stringify(box)}`);
      }
      const row = page.locator(".fileRow", { hasText: "A phone title" });
      const name = await row.locator(".fileRowName").boundingBox();
      const chips = await row.locator(".fileRowLabels").boundingBox();
      assert(name.height > 30, `a long title wraps: ${name.height}`);
      assert(chips.y >= name.y + name.height - 1, "the chips sit under the title");
      // More: the account menu with background tasks and the View menu's
      // rows on top; a row opens its own popover in the sheet's place.
      await page.getByRole("button", { name: "More", exact: true }).tap();
      const sheet = page.locator(".userPopover");
      await sheet.getByRole("button", { name: "Import…", exact: true }).waitFor();
      await sheet.getByRole("button", { name: "Settings…", exact: true }).waitFor();
      await sheet.getByRole("button", { name: "Background tasks", exact: true }).tap();
      await page.locator(".downloadsPopover").waitFor();
      assertEq(await sheet.count(), 0, "the sheet gave way to the tasks");
      await page.keyboard.press("Escape");
      await page.locator(".downloadsPopover").waitFor({ state: "detached" });
      // A page: its tab brings the topbar back, Share joins the bar, and
      // the Library tab goes home.
      await row.tap();
      await until(() => page.url().includes(`block=${note.id}`), { what: "a tap opens the page" });
      await page.locator(".app > .topbar").waitFor();
      assertEq(await barWords(), "Library Notes Chat Add Search Share More");
      await page.getByRole("button", { name: "Library", exact: true }).tap();
      await page.waitForSelector(".folderNewBtn");
      assert(!page.url().includes("block="), `the Library tab went home: ${page.url()}`);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });
}
