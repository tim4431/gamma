// The installed web app (docs/dev/ipad.md): the manifest and its icons, the
// home-screen head tags, theme-color following the theme, the
// standalone-mode stylesheet, the layout by orientation (upright: the
// compact shell; landscape: the docks), and the editing bar a touch screen's
// note editor brings; and the iPad app's shell over it (shared/lib/host.js),
// played by a stand-in host: the Pencil's claim in the preset's look and the
// stroke handed back, the hold and its answer, the theme message, the
// Pencil gestures with their choices and phases, the haptics, the Apple
// Pencil settings and the View menu's row for the shell's own bar. Chromium
// in a tablet-sized touch context — emulation, not an iPad.
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { Account, ROOT } from "../harness.mjs";
import { decodeStroke } from "../../../src/ink/ink.js";
import { newInkStrokes, savedInkStrokes } from "./ink.mjs";
import { same, tree } from "./notes.mjs";
import { waitForPdf } from "./pdf.mjs";

// What the iPad app's user script declares before the bundle runs (HostInfo,
// every flag this build's shell declares; `flags` overrides them, e.g.
// {hold: false} for an older shell), with a message handler that records
// what the page posts, a haptic included, and takes every claim. `files`
// (not a flag: [{id, name, type, b64}]) are what the system opened in the
// app, held as OpenedFiles.swift holds them: listed, read in chunks (of 1000
// bytes here, so a file takes several) and let go by `fileDone`. Like the
// shell, it still holds them after a reload (the init script runs again);
// `window.e2eHold(file)` adds one later, as a share while the page is up
// does, and `window.e2eHeld` is what it holds. `bar` (not a flag either) is
// the person's choice of the shell's bar as it starts, true for up, which
// `shellBar` sets and answers as AppModel.barHidden does.
function fakeShell(options = {}) {
  const { files = [], bar = true, ...flags } = options;
  let barShown = bar;
  const sent = [];
  window.e2eHostSent = sent;
  const withBytes = (f) => ({ ...f, bytes: Uint8Array.from(atob(f.b64), (c) => c.charCodeAt(0)) });
  const held = files.map(withBytes);
  window.e2eHeld = held;
  window.e2eHold = (f) => { held.push(withBytes(f)); };
  const native = { platform: "ipad", version: "0.2.0", ink: true,
    inkLooks: ["pressure", "dash", "blend", "stabilize", "pencil", "calligraphy", "snap"],
    hold: true, pencil: true, pencilPhases: true, pencilRoll: true, haptics: true, openFiles: true, shellBar: true, ...flags };
  native.receive = (msg) => new Promise((resolve, reject) => (native.queue ||= []).push({ msg, resolve, reject }));
  window.gammaNative = native;
  const answer = (msg) => {
    if (msg.type === "claim") return { taken: true };
    if (msg.type === "files") return { files: held.map(({ id, name, type, bytes }) => ({ id, name, type, size: bytes.length })) };
    if (msg.type === "fileData") {
      const file = held.find((f) => f.id === msg.id);
      if (!file) return {};
      let bin = "";
      for (const b of file.bytes.subarray(msg.offset, msg.offset + 1000)) bin += String.fromCharCode(b);
      return { data: btoa(bin) };
    }
    if (msg.type === "fileDone") {
      const i = held.findIndex((f) => f.id === msg.id);
      if (i >= 0) held.splice(i, 1);
    }
    if (msg.type === "shellBar") {
      if (typeof msg.shown === "boolean") barShown = msg.shown;
      return { shown: barShown };
    }
    return {};
  };
  window.webkit = { messageHandlers: { gamma: { postMessage: (msg) => {
    if (msg.type !== "fileData") sent.push(JSON.parse(JSON.stringify(msg)));
    return Promise.resolve(answer(msg));
  } } } };
}
// An iPad's own Safari, which would be offered Add to Home Screen; the shell never is.
const SHELL_UA = "Mozilla/5.0 (iPad; CPU OS 18_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Mobile/15E148 Safari/604.1";

export async function ipadScenarios({ server, browser, alice, makePdf, step, until, sleep, assert, assertEq, assertNoProblems, openPage }) {
  const python = process.env.GAMMA_E2E_PYTHON || path.join(ROOT, "backend", "venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
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
      await page.getByRole("menuitem", { name: "Settings…", exact: true }).click();
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
      // bundled stylesheet's full-screen rule: html[data-shell], which
      // shared/lib/host.js sets for the installed app and the iPad shell.
      const shellRules = await page.evaluate(() => {
        const out = [];
        for (const sheet of document.styleSheets) {
          let rules; try { rules = sheet.cssRules; } catch { continue; }
          for (const rule of rules) {
            if (rule instanceof CSSStyleRule && /^html\[data-shell\]/.test(rule.selectorText)) out.push(`${rule.selectorText} { ${rule.style.cssText} }`);
          }
        }
        return out;
      });
      assertEq(await page.locator("html").getAttribute("data-shell"), null, "a browser tab is no shell");
      // The document never rubber-bands or pulls to refresh, in a tab as installed.
      assertEq(await page.evaluate(() => getComputedStyle(document.documentElement).overscrollBehaviorY), "none", "document overscroll");
      assert(shellRules.some((r) => /\.app\b/.test(r) && /safe-area-inset-bottom/.test(r)), `home indicator inset: ${shellRules.join(" | ")}`);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("ipad: a tap opens a library folder and page; a mouse click only selects", async () => {
    const paper = await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Tap to open paper" } });
    await alice.file(paper.id, { folders: ["tapfolder"] });
    const tapFolder = await alice.folder("tapfolder");
    const ctx = await alice.context(browser, { hasTouch: true, viewport: { width: 834, height: 1194 } });
    try {
      const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
      const folder = page.locator(".pageCard, .folderRow", { hasText: "tapfolder" }).first();
      await folder.click();
      await until(() => folder.evaluate((el) => el.classList.contains("selected")), { what: "a mouse click selects the folder" });
      assert(!page.url().includes("folder="), `a mouse click stays on the library: ${page.url()}`);
      await folder.tap();
      await until(() => page.url().includes(`folder=${tapFolder}`), { what: "a tap opens the folder" });
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

  // The iPad app's shell (docs/dev/ipad.md): the web decides that a Pencil
  // touch is a stroke and claims it; the host's {taken: true} ends the page's
  // draft (whatever WebKit sends the pointer next), and the host hands the
  // finished samples back in viewport CSS px, which commit as the browser's
  // own stroke. The device side (the
  // recognizer, the live line) is the Swift tests' and the device checks'.
  await step("ipad shell: a Pencil stroke is claimed and committed from the host's samples; theme, gestures", async () => {
    const up = await alice.upload("/api/uploads", makePdf([["A page the Pencil writes on."]]), "shell.pdf", "application/pdf");
    const paper = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Shell paper", source_url: up.source_url } });
    const ctx = await alice.context(browser, { hasTouch: true, viewport: { width: 1194, height: 834 }, userAgent: SHELL_UA });
    await ctx.addInitScript(fakeShell);
    try {
      const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}&block=${paper.id}`);
      await waitForPdf(page, 1);
      const sent = (type) => page.evaluate((type) => window.e2eHostSent.filter((m) => m.type === type), type);
      assertEq(await page.locator("html").getAttribute("data-host"), "ipad");
      assertEq(await page.locator("html").getAttribute("data-shell"), "ipad", "the shell takes the full-screen rules");

      // theme: the colour under the status bar, the scheme for its style, and
      // the theme's name for the shell's bar and launcher.
      const theme = (await sent("theme")).at(-1);
      assert(theme, "a theme message");
      assertEq(theme.bg, await page.locator('meta[name="theme-color"]').getAttribute("content"), "the window's colour is the status bar's");
      assertEq(theme.scheme, await page.locator("html").getAttribute("data-scheme"));
      assertEq(theme.name, await page.locator("html").getAttribute("data-theme"), "the theme's name");
      assert(/^#[0-9a-f]{6}$/.test(theme.bg), `a hex colour: ${theme.bg}`);

      await page.click("button[aria-label='Handwriting tools']");
      await page.waitForSelector(".pdfInkBar .inkToolBtn.modeActive .inkToolInk");
      const paths = '[data-page="1"] .inkLayer path';
      const count = await page.locator(paths).count();
      const before = new Set((await alice.api(`/api/blocks/${paper.id}/subtree`)).block.children.map((b) => b.id));
      // The Pencil goes down and moves: the page draws its draft and claims
      // the touch, with where it went down; the host takes it.
      const down = await page.locator('[data-page="1"]').evaluate((el) => {
        const box = el.getBoundingClientRect();
        const send = (type, x) => el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true,
          pointerType: "pen", pointerId: 61, button: 0, buttons: 1, pressure: 0.5, clientX: box.left + x, clientY: box.top + 300 }));
        send("pointerdown", 100);
        send("pointermove", 110);
        return { left: box.left, top: box.top, k: box.width / el.querySelector(".inkLayer").viewBox.baseVal.width };
      });
      const claim = await until(async () => (await sent("claim"))[0], { what: "the stroke is claimed" });
      assertEq(claim.pointerId, 61);
      assertEq(claim.tool.kind, "pen");
      assert(/^#/.test(claim.tool.color) && claim.tool.opacity === 1, `the tool's colour and opacity: ${JSON.stringify(claim.tool)}`);
      assert(claim.tool.size > 0, "the width on screen");
      assertEq(JSON.stringify(claim.at), JSON.stringify({ x: down.left + 100, y: down.top + 300 }), "where the pointer went down");
      await until(async () => (await page.locator('[data-page="1"] .inkCanvas').evaluate((c) => c.style.display)) === "none",
        { what: "the draft gives way to the host's line on the reply" });
      // WebKit may still deliver the pointer's own events: they save nothing.
      await page.locator('[data-page="1"]').evaluate((el) => {
        const box = el.getBoundingClientRect();
        for (const type of ["pointermove", "pointerup"]) {
          el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerType: "pen", pointerId: 61,
            button: 0, buttons: type === "pointerup" ? 0 : 1, pressure: 0.5, clientX: box.left + 120, clientY: box.top + 300 }));
        }
      });
      assertEq(await page.locator(paths).count(), count, "the page's own pointer-up commits nothing");
      // The lift: the host's samples, viewport px and ms from the first; the
      // repeated last point is dropped as the pointer path drops it.
      const { left, top, k } = down;
      const samples = [[100, 0.2, 0], [110, 0.4, 4], [120, 0.6, 8], [130, 0.8, 12], [130, 0.8, 16]]
        .map(([x, p, t]) => [left + x, top + 300, p, t]);
      const stroke = (samples) => window.gammaNative.receive({ type: "stroke", pointerId: 61, samples });
      assertEq(JSON.stringify(await page.evaluate(stroke, samples)), JSON.stringify({ committed: true }));
      assertEq(await page.locator(paths).count(), count + 1, "the stroke is on the page by the reply");
      const block = await until(async () => {
        const d = await alice.api(`/api/blocks/${paper.id}/subtree`);
        return d.block.children.find((b) => !before.has(b.id) && b.properties?.ink_url);
      }, { what: "the host's stroke saved as an ink block" });
      const ink = await alice.api(block.properties.ink_url);
      const st = ink.strokes[0];
      assertEq(st.ch, "xyptw");
      assert(st.pen === true && typeof st.t0 === "number", "a pen stroke with its wall clock");
      assertEq(st.pts.length, 20, "four samples, the repeat dropped");
      assertEq(JSON.stringify(st.pts.filter((_, i) => i % 5 === 2)), "[200,400,600,800]", "pressure");
      assertEq(JSON.stringify(st.pts.filter((_, i) => i % 5 === 3)), "[0,4,4,4]", "time deltas");
      assertEq(JSON.stringify(st.pts.filter((_, i) => i % 5 === 4)), "[700,200,200,200]", "the width baked by the web app, like its own strokes");
      const x = st.pts.filter((_, i) => i % 5 === 0).reduce((a, b) => a + b, 0) / 100;
      const y = st.pts.filter((_, i) => i % 5 === 1).reduce((a, b) => a + b, 0) / 100;
      assert(Math.abs(x - 130 / k) < 0.02 && Math.abs(y - 300 / k) < 0.02, `page units from the down's rect and zoom: ${x}, ${y}`);
      // The same pointer again, no longer claimed, commits nothing.
      assertEq(JSON.stringify(await page.evaluate(stroke, samples)), JSON.stringify({ committed: false }));
      // A touch the system cancelled: the host's stroke has no samples, which
      // ends the claim and saves nothing.
      await page.locator('[data-page="1"]').evaluate((el) => {
        const box = el.getBoundingClientRect();
        el.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, cancelable: true, pointerType: "pen",
          pointerId: 62, button: 0, buttons: 1, pressure: 0.5, clientX: box.left + 200, clientY: box.top + 400 }));
      });
      await until(async () => (await sent("claim")).some((m) => m.pointerId === 62), { what: "the second stroke is claimed" });
      const abandoned = await page.evaluate(() => window.gammaNative.receive({ type: "stroke", pointerId: 62, samples: [] }));
      assertEq(JSON.stringify(abandoned), JSON.stringify({ committed: false }));
      assertEq(await page.locator(paths).count(), count + 1, "nothing saved for a cancelled touch");

      // The Pencil's double tap with the system's "switch to the eraser": on, then back.
      const armed = () => page.locator(".pdfInkBar .inkToolBtn.modeActive").getAttribute("aria-label");
      const pen = await armed();
      const gesture = (action) => page.evaluate((action) => window.gammaNative.receive({ type: "pencil", gesture: "tap", action, at: null }), action);
      await gesture("switchEraser");
      await page.waitForSelector(".pdfInkBar button[title^='Eraser'].modeActive");
      await gesture("switchEraser");
      await until(async () => (await armed()) === pen, { what: "the double tap again goes back to the pen" });
      await gesture("showColorPalette");
      await page.waitForSelector(".pdfInkSub[data-ink-options='tool']");
      await gesture("ignore");
      assertEq(await armed(), pen, "an ignored gesture changes nothing");

      // No install offer under the shell.
      assertEq(await page.getByText("Add to Home Screen").count(), 0, "no install offer");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // --- the shell: files the system opened in the app (docs/dev/ipad.md "Opened files") ---
  await step("ipad shell: files the app was given open as picked ones do: a PDF its page, Markdown a note, an .ntb the Notability review, a .zip the Import dialog; a workspace others see asks first, a read-only one sends them to the personal workspace", async () => {
    // Accounts of its own: the workspaces it shares, and the notices they
    // bring, stay out of the steps that follow. A server admin makes the
    // shared workspaces, then stops being one.
    for (const [name, pw] of [["ip-admin", "ip-admin-pw"], ["ip-ann", "ip-ann-pw"], ["ip-ben", "ip-ben-pw"]]) {
      await server.manage("create-user", name, pw);
    }
    const ann = await new Account(server, "ip-ann", "ip-ann-pw").login();
    const ben = await new Account(server, "ip-ben", "ip-ben-pw").login();
    const b64 = (bytes) => Buffer.from(bytes).toString("base64");
    const pdfFile = (id, name, text) => ({ id, name, type: "application/pdf", b64: b64(makePdf([[text]])) });
    const pages = async (who = ann) => (await who.api("/api/blocks/root/children")).children;
    const fileDone = (page, id) => until(async () => (await page.evaluate(() => window.e2eHostSent))
      .some((m) => m.type === "fileDone" && m.id === id), { what: `the shell lets ${id} go` });
    const hand = (page, file) => page.evaluate((file) => { window.e2eHold(file); return window.gammaNative.receive({ type: "filesOpened" }); }, file);
    // An .ntb and a .zip of a Markdown note, as the transfer group makes them.
    const dir = fs.mkdtempSync(path.join(server.dir, "opened-"));
    const ntbPath = path.join(dir, "Handed.ntb");
    const zipPath = path.join(dir, "Handed vault.zip");
    execFileSync(python, ["-c", `
import sys, zipfile
sys.path.insert(0, 'backend/tests')
import ntb_fixtures as tx
open(sys.argv[1], 'wb').write(tx.ntb_zip(tx.bundle((1, tx.DOCUMENT, tx.document("Handed NTB note")),
    (8, (0, 11), tx.text("Typed on the iPad")))))
with zipfile.ZipFile(sys.argv[2], 'w') as z:
    z.writestr('Vault/Vault note.md', '# Vault note\\n\\nFrom a zipped vault.\\n')
`, ntbPath, zipPath], { cwd: ROOT });
    const personal = (await ann.api("/api/session")).workspaces.find((w) => w.personal);

    // This iPad's own library: a PDF waiting when the page loads becomes its
    // page at the library's top, opened, and the shell lets it go.
    let ctx = await ann.context(browser, { hasTouch: true, viewport: { width: 1194, height: 834 }, userAgent: SHELL_UA });
    await ctx.addInitScript(fakeShell, { files: [pdfFile("F1", "Handed paper.pdf", "A paper the iPad was handed.")] });
    try {
      const page = await openPage(ctx, `${server.base}/?ws=${ann.ws}`);
      await waitForPdf(page, 1);
      await fileDone(page, "F1");
      const paper = (await pages()).find((p) => p.content === "Handed paper.pdf");
      assert(paper, `the PDF is a page titled with its name: ${(await pages()).map((p) => p.content).join(" | ")}`);
      assertEq((paper.properties?.folders || []).length, 0, "at the library's top");
      await until(async () => (await page.locator(".titleText").first().innerText().catch(() => "")) === "Handed paper.pdf",
        { what: "its page is the one open" });

      // Shared while the page is up: the shell says so and the page asks.
      await hand(page, { id: "F2", name: "Handed note.md", type: "text/markdown", b64: b64("# Handed note\n\nWritten in another app.\n") });
      await fileDone(page, "F2");
      await page.getByText("Written in another app.").first().waitFor();
      assert((await pages()).some((p) => p.content === "Handed note"), `the Markdown is a note page: ${(await pages()).map((p) => p.content).join(" | ")}`);

      // A Notability .ntb: the Notability import's review, as Import → Notability opens it.
      await hand(page, { id: "F3", name: "Handed.ntb", type: "", b64: b64(fs.readFileSync(ntbPath)) });
      await fileDone(page, "F3");
      let review = page.getByRole("dialog", { name: "Review Notability import", exact: true });
      await review.getByRole("region", { name: "Library after import", exact: true }).getByText("Handed NTB note", { exact: true }).waitFor();
      await review.getByRole("button", { name: "Import to library", exact: true }).click();
      let report = page.getByRole("dialog", { name: "Import complete", exact: true });
      await report.waitFor();
      await report.getByRole("button", { name: "Done", exact: true }).click();
      assert((await pages()).some((p) => p.content === "Handed NTB note"), "the note is a page");

      // A .zip: the Import dialog asks which export it is, offering only the
      // sources that read an archive, and imports it with no picker.
      await hand(page, { id: "F4", name: "Handed vault.zip", type: "application/zip", b64: b64(fs.readFileSync(zipPath)) });
      await fileDone(page, "F4");
      const chooser = page.getByRole("dialog", { name: "Import Handed vault.zip", exact: true });
      await chooser.waitFor();
      assertEq(await chooser.getByText("Logseq highlights", { exact: true }).count(), 0, "Logseq reads no archive");
      await chooser.getByText("Markdown notes", { exact: true }).click();
      await chooser.getByRole("button", { name: "Import", exact: true }).click();
      review = page.getByRole("dialog", { name: "Review Markdown import", exact: true });
      await review.getByRole("region", { name: "Library after import", exact: true }).getByText("Vault note", { exact: true }).first().waitFor();
      await review.getByRole("button", { name: "Import to library", exact: true }).click();
      report = page.getByRole("dialog", { name: "Import complete", exact: true });
      await report.waitFor();
      await report.getByRole("button", { name: "Done", exact: true }).click();
      await until(async () => (await pages()).some((p) => p.content === "Vault note"), { what: "the vault's note is a page" });

      // A file Gamma does not open is let go, and the status says so.
      await hand(page, { id: "F5", name: "photo.heic", type: "image/heic", b64: b64("not a picture") });
      await fileDone(page, "F5");
      await page.getByRole("status").filter({ hasText: "Gamma does not open photo.heic." }).waitFor();
      assertEq(await page.evaluate(() => window.e2eHeld.length), 0, "the shell holds nothing more");
      assertNoProblems(page);
    } finally { await ctx.close(); }

    // A shared workspace asks first, as the desktop does. Cancel lets the
    // file go and adds nothing; the personal workspace's button takes it
    // there, the page reloading on that workspace with the file still held.
    // Ann's lab, which Ben edits, and Ben's reading room, which Ann only reads.
    await server.manage("set-admin", "ip-admin", "on");
    const admin = await new Account(server, "ip-admin", "ip-admin-pw").login();
    const lab = await admin.api("/api/workspaces", { method: "POST", body: { name: "Handed lab", kind: "shared", owner: "ip-ann" } });
    const reading = await admin.api("/api/workspaces", { method: "POST", body: { name: "Ben's reading room", kind: "shared", owner: "ip-ben" } });
    await server.manage("set-admin", "ip-admin", "off");
    await ann.api(`/api/workspaces/${lab.id}/members/ip-ben`, { method: "PUT", body: { role: "editor" } });
    await ben.api(`/api/workspaces/${reading.id}/members/ip-ann`, { method: "PUT", body: { role: "viewer" } });
    const asked = async (file) => {
      const c = await ann.context(browser, { hasTouch: true, viewport: { width: 1194, height: 834 }, userAgent: SHELL_UA });
      await c.addInitScript(fakeShell, { files: [file] });
      const page = await openPage(c, `${server.base}/?ws=${lab.id}`);
      const box = page.locator(".confirmModal");
      await box.getByText(`Add “${file.name}” to Handed lab?`).waitFor();
      await box.getByText("Handed lab is shared: everyone in it can see what you add.").waitFor();
      return { c, page, box };
    };
    let shown = await asked(pdfFile("F6", "Lab paper.pdf", "A paper for the lab, then not."));
    try {
      const { page, box } = shown;
      await box.getByRole("button", { name: "Cancel", exact: true }).click();
      await fileDone(page, "F6");
      await page.getByRole("status").filter({ hasText: "Not added: “Lab paper.pdf”" }).waitFor();
      assert(!(await pages()).some((p) => p.content === "Lab paper.pdf"), "nothing added");
      assertNoProblems(page);
    } finally { await shown.c.close(); }
    shown = await asked(pdfFile("F7", "Mine after all.pdf", "A paper that goes to the personal workspace."));
    try {
      const { page, box } = shown;
      await box.getByRole("button", { name: `Add to ${personal.name}`, exact: true }).click();
      await page.waitForURL((url) => url.searchParams.get("ws") === personal.id);
      await waitForPdf(page, 1);
      await fileDone(page, "F7");
      assert((await pages()).some((p) => p.content === "Mine after all.pdf"), "in the personal workspace");
      assertNoProblems(page);
    } finally { await shown.c.close(); }

    // A workspace the account can only read: the files go to the personal
    // one at once, and the status says why after the reload.
    ctx = await ann.context(browser, { hasTouch: true, viewport: { width: 1194, height: 834 }, userAgent: SHELL_UA });
    await ctx.addInitScript(fakeShell, { files: [pdfFile("F8", "Read only.pdf", "A paper handed to a reader.")] });
    try {
      const page = await openPage(ctx, `${server.base}/?ws=${reading.id}`);
      await page.waitForURL((url) => url.searchParams.get("ws") === personal.id);
      await waitForPdf(page, 1);
      await fileDone(page, "F8");
      await page.getByRole("status").filter({ hasText: `“Read only.pdf” went into ${personal.name}: you can only read Ben's reading room.` }).waitFor();
      assert((await pages()).some((p) => p.content === "Read only.pdf"), "in the personal workspace");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // --- the shell: the claim's look, the hold, the gestures' choices, the haptics ---
  // A PDF page in a shell context (the stand-in with `flags`), the strip open
  // with the stylus pen, and the shell's side played in the page: the Pencil
  // going down on page 1 (the page claims it, the stand-in takes it), the
  // host's `hold` and `stroke` with samples [x, y, p, t, a?, z?] in px from
  // the page's corner (a `stroke` naming, as `held`, the hold whose shape
  // the shell drew), and what the page posted.
  async function shellPaper(name, flags) {
    const up = await alice.upload("/api/uploads", makePdf([[`A page the Pencil ${name} on.`]]), `${name}.pdf`, "application/pdf");
    const paper = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: `Shell ${name}`, source_url: up.source_url } });
    const ctx = await alice.context(browser, { hasTouch: true, viewport: { width: 1194, height: 834 }, userAgent: SHELL_UA });
    await ctx.addInitScript(fakeShell, flags || {});
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}&block=${paper.id}`);
    await waitForPdf(page, 1);
    await page.click("button[aria-label='Handwriting tools']");
    await page.waitForSelector(".pdfInkBar .inkToolBtn.modeActive .inkToolInk");
    const pdf = page.locator('[data-page="1"]');
    const geometry = () => pdf.evaluate((el) => {
      const box = el.getBoundingClientRect();
      return { left: box.left, top: box.top, k: box.width / el.querySelector(".inkLayer").viewBox.baseVal.width };
    });
    const sent = (type) => page.evaluate((type) => window.e2eHostSent.filter((m) => m.type === type), type);
    const receive = (msg) => page.evaluate((msg) => window.gammaNative.receive(msg), msg);
    const toViewport = async (rows) => { const g = await geometry(); return rows.map(([x, y, ...rest]) => [g.left + x, g.top + y, ...rest]); };
    const down = async (pointerId, x, y) => {
      const claims = (await sent("claim")).length;
      await pdf.evaluate((el, [pointerId, x, y]) => {
        const box = el.getBoundingClientRect();
        el.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, cancelable: true, pointerType: "pen", pointerId,
          button: 0, buttons: 1, pressure: 0.5, clientX: box.left + x, clientY: box.top + y }));
      }, [pointerId, x, y]);
      const claim = await until(async () => (await sent("claim")).slice(claims).find((m) => m.pointerId === pointerId),
        { what: `pointer ${pointerId} claimed` });
      await until(async () => (await pdf.locator(".inkCanvas").evaluate((c) => c.style.display)) === "none", { what: "the draft handed over" });
      return claim;
    };
    const shell = { pdf, geometry, sent, receive, toViewport, down,
      hold: async (pointerId, rows) => receive({ type: "hold", pointerId, samples: await toViewport(rows) }),
      stroke: async (pointerId, rows, held) => receive({ type: "stroke", pointerId, samples: await toViewport(rows), ...(held == null ? {} : { held }) }) };
    return { ctx, page, paper, shell };
  }
  // The strokes saved on a page (ink.mjs), and the ones saved since.
  const savedStrokes = (paperId) => savedInkStrokes(alice, paperId);
  const newStrokes = (paperId, before, n, what) => newInkStrokes(alice, paperId, before, n, what);
  // A line drawn left to right over 300 ms at `y`, then held still to 780 ms
  // (six-value samples, the Pencil at 50°): the stillness anchor is its end
  // at 300 ms, so the hold's snap time is 900 ms.
  const heldLine = (y) => [
    ...Array.from({ length: 16 }, (_, i) => [100 + 10 * i, y + (i % 2 ? 0.6 : -0.6), 0.5, 20 * i, 50, 90]),
    ...Array.from({ length: 6 }, (_, j) => [250 + (j % 2 ? 0.5 : -0.25), y + (j % 2 ? 0.25 : -0.25), 0.5, 380 + 80 * j, 50, 90]),
  ];
  const T_SNAP = 900;
  const LOOKS = [
    { id: "pen1", kind: "pen", color: "#1f4fd8", size: 2, brush: "brush", dash: "dashed", stabilize: 2 },
    { id: "pen2", kind: "pen", color: "#222222", size: 2, brush: "pencil" },
    { id: "pen3", kind: "pen", color: "#c2410c", size: 2 },
    { id: "hl1", kind: "highlighter", color: "#fde047", size: 14 },
  ];

  await step("ipad shell: the claim carries the preset's look, and a Pencil preset's six-value samples commit with their tilt", async () => {
    await alice.api("/api/prefs/profile", { method: "PATCH", body: { set: { inkTools: LOOKS } } });
    let ctx;
    try {
      const opened = await shellPaper("styles");
      ctx = opened.ctx;
      const { page, paper, shell } = opened;
      const { k } = await shell.geometry();
      const near = (a, b) => Math.abs(a - b) < 1e-6;
      const keys = (tool) => JSON.stringify(Object.keys(tool).sort());
      // preset 1: a dashed Brush that smooths
      await page.keyboard.press("1");
      let claim = await shell.down(71, 100, 200);
      assertEq(keys(claim.tool), JSON.stringify(["brush", "color", "dash", "kind", "opacity", "size", "stabilize"]), "the look's keys");
      assert(claim.tool.brush === "brush" && claim.tool.stabilize === 2 && near(claim.tool.size, 2 * k), JSON.stringify(claim.tool));
      assert(near(claim.tool.dash[0], 2 * claim.tool.size) && near(claim.tool.dash[1], 2 * claim.tool.size),
        `dashed: two sizes on, two off, in css px (${claim.tool.dash})`);
      assertEq(JSON.stringify(await shell.stroke(71, [])), JSON.stringify({ committed: false }));
      // preset 3: the plain pen claims the four keys an older shell reads
      await page.keyboard.press("3");
      claim = await shell.down(72, 100, 200);
      assertEq(keys(claim.tool), JSON.stringify(["color", "kind", "opacity", "size"]), "a plain pen: nothing of a look");
      await shell.stroke(72, []);
      // preset 4: a highlighter multiplies onto the page
      await page.keyboard.press("4");
      claim = await shell.down(73, 100, 200);
      assert(claim.tool.kind === "highlighter" && claim.tool.blend === "multiply" && !("brush" in claim.tool), JSON.stringify(claim.tool));
      await shell.stroke(73, []);
      // preset 2: a Pencil; the host's samples carry the tilt in degrees
      await page.keyboard.press("2");
      claim = await shell.down(74, 150, 400);
      assertEq(claim.tool.brush, "pencil");
      const before = await savedStrokes(paper.id);
      const rows = Array.from({ length: 12 }, (_, i) => [150 + 8 * i, 400, 0.3 + 0.05 * i, 8 * i, 40.2, 119.6]);
      assertEq(JSON.stringify(await shell.stroke(74, rows)), JSON.stringify({ committed: true }));
      const [pencil] = await newStrokes(paper.id, before, 1, "the Pencil stroke saved");
      assertEq(JSON.stringify([pencil.brush, pencil.ch]), JSON.stringify(["pencil", "xyptazwo"]), "pressure, tilt, width and shade");
      assert(decodeStroke(pencil).every((q) => q.a === 40 && q.z === 120), "the tilt as integer degrees on every sample");
      assertNoProblems(page);
    } finally {
      await ctx?.close();
      await alice.api("/api/prefs/profile", { method: "PATCH", body: { set: { inkTools: null } } }).catch(() => {});
    }
  });

  await step("ipad shell: a calligraphy pen claims its nib, and the roll the shell's first sample carries turns the committed nib; with Lock angle, which shows under pencilRoll, it does not", async () => {
    await alice.api("/api/prefs/profile", { method: "PATCH", body: { set: { inkTools: [
      { id: "cal1", kind: "pen", color: "#1f1f1f", size: 2, brush: "calligraphy" },   // smoothing left out: a calligraphy pen's 2
      { id: "cal2", kind: "pen", color: "#1f4fd8", size: 2, brush: "calligraphy", nib: -30, nibLock: true },
    ] } } });
    let ctx;
    try {
      const opened = await shellPaper("calligraphy");
      ctx = opened.ctx;
      const { page, paper, shell } = opened;
      // preset 1: the claim names the brush, the nib (45 to start), that it follows the roll and the smoothing it draws with
      await page.keyboard.press("1");
      let claim = await shell.down(81, 100, 200);
      assert(claim.tool.brush === "calligraphy" && claim.tool.nib === 45 && claim.tool.nibRoll === true && claim.tool.stabilize === 2,
        JSON.stringify(claim.tool));
      // a stroke down to the right, the Pencil rolled 30° clockwise: [x, y, p, t, a, z, r]
      const rows = Array.from({ length: 12 }, (_, i) => [100 + 6 * i, 200 + 6 * i, 0.5, 8 * i, 60, 45, 30]);
      let before = await savedStrokes(paper.id);
      assertEq(JSON.stringify(await shell.stroke(81, rows)), JSON.stringify({ committed: true }));
      const [rolled] = await newStrokes(paper.id, before, 1, "the rolled calligraphy stroke saved");
      assertEq(JSON.stringify([rolled.brush, rolled.nib, rolled.ch]), JSON.stringify(["calligraphy", 15, "xyptazw"]),
        "45° less the 30° clockwise roll; its widths baked from the travel against that nib");
      // preset 2 locks its angle (−30°, a stroke's 150°): no nibRoll, and a roll changes nothing
      await page.keyboard.press("2");
      claim = await shell.down(82, 100, 300);
      assert(claim.tool.nib === 150 && !("nibRoll" in claim.tool), JSON.stringify(claim.tool));
      before = await savedStrokes(paper.id);
      await shell.stroke(82, rows.map(([x, y, ...rest]) => [x, y + 100, ...rest]));
      const [locked] = await newStrokes(paper.id, before, 1, "the locked calligraphy stroke saved");
      assertEq(locked.nib, 150, "the preset's angle, whatever the roll");
      // its options row: the Nib angle and, under a shell that reports the roll, Lock angle pressed
      await page.locator(".pdfInkBar .inkToolBtn.modeActive").first().click();
      const lock = page.locator('[data-ink-options="tool"] button[aria-label="Lock angle"]');
      await lock.waitFor();
      assertEq(await lock.getAttribute("aria-pressed"), "true");
      assertEq((await page.locator('[data-ink-options="tool"] .inkNibReadout').textContent()).trim(), "-30°");
      assertNoProblems(page);
    } finally {
      await ctx?.close();
      await alice.api("/api/prefs/profile", { method: "PATCH", body: { set: { inkTools: null } } }).catch(() => {});
    }
  });

  await step("ipad shell: the host's hold snaps a still line (css px, a snap tick), the lift commits it refit; a quick move commits freehand, as does a lift that beat the answer; with strokes copied a still pen opens Paste here", async () => {
    let ctx;
    try {
      const opened = await shellPaper("holds");
      ctx = opened.ctx;
      const { page, paper, shell } = opened;
      const { left, top, k } = await shell.geometry();
      const line = heldLine(300);
      await shell.down(81, 100, 300);
      let before = await savedStrokes(paper.id);
      const answer = await shell.hold(81, line);
      assertEq(answer?.shape?.kind, "line", `the hold answers a line (${JSON.stringify(answer)})`);
      const p = answer.shape.params;
      assert(Math.abs(p.x1 - (left + 100)) < 2 && Math.abs(p.x2 - (left + 250)) < 2 && Math.abs(p.y1 - (top + 300)) < 1.5 && p.y1 === p.y2,
        `in viewport css px, snapped level (${JSON.stringify(p)})`);
      const [from] = await shell.toViewport([line.at(-1)]);
      const snap = (await shell.sent("haptic")).at(-1);
      assert(snap?.kind === "snap" && Math.abs(snap.at.x - from[0]) < 0.011 && Math.abs(snap.at.y - from[1]) < 0.011,
        `a snap ticks at the hold's last sample (${JSON.stringify(snap)})`);
      assertEq(JSON.stringify(await shell.hold(81, line)), "{}", "a claim keeps one shape: a later hold answers nothing");
      // the pen moves on slowly after the snap: the line's end follows it to the lift
      const after = Array.from({ length: 10 }, (_, i) => [250 + 4 * (i + 1), 300 + 3 * (i + 1), 0.5, T_SNAP + 200 + 40 * i, 50, 90]);
      assertEq(JSON.stringify(await shell.stroke(81, [...line, ...after], line.length)), JSON.stringify({ committed: true }));
      const [held] = await newStrokes(paper.id, before, 1, "the held line saved");
      assertEq(JSON.stringify([held.shape?.kind, held.ch, held.pen]), JSON.stringify(["line", "xyt", false]), "a shape: constant width, no pressure");
      const [a, b] = decodeStroke(held);
      assert(Math.abs(b.x - 290 / k) < 0.02 && Math.abs(b.y - 330 / k) < 0.02, `refit to the last sample, in page points (${b.x}, ${b.y})`);
      assert(Math.abs(a.x - (p.x1 - left) / k) < 0.02 && Math.abs(a.y - (p.y1 - top) / k) < 0.02, "its start where it snapped");
      assert(Math.abs(held.shape.params.x2 - b.x) < 0.011 && Math.abs(held.shape.params.y2 - b.y) < 0.011, "its shape record follows its samples");
      await until(async () => (await page.locator(".statusPill").allInnerTexts()).join(" ").includes("Straightened."), { what: "the pill says so" });
      // a quick move right after the snap drops it: the stroke is freehand
      const line2 = heldLine(500);
      await shell.down(82, 100, 500);
      before = await savedStrokes(paper.id);
      assertEq((await shell.hold(82, line2))?.shape?.kind, "line");
      const quick = Array.from({ length: 6 }, (_, i) => [250 + 6 * (i + 1), 500, 0.5, T_SNAP + 20 * (i + 1), 50, 90]);
      assertEq(JSON.stringify(await shell.stroke(82, [...line2, ...quick], line2.length)), JSON.stringify({ committed: true }));
      const [free] = await newStrokes(paper.id, before, 1, "the freehand stroke saved");
      assert(!free.shape && free.ch === "xyptazw", `freehand, with its pressure and tilt (${free.ch})`);
      // the lift came before the shell had the answer: it drew the freehand line and names no hold, and the page keeps to it
      const line3 = heldLine(400);
      await shell.down(84, 100, 400);
      before = await savedStrokes(paper.id);
      assertEq((await shell.hold(84, line3))?.shape?.kind, "line", "the page answers, and keeps, the shape");
      assertEq(JSON.stringify(await shell.stroke(84, line3)), JSON.stringify({ committed: true }));
      const [raced] = await newStrokes(paper.id, before, 1, "the stroke whose lift beat the answer saved");
      assert(!raced.shape && raced.ch === "xyptazw", `freehand, as the shell drew it (${raced.ch})`);
      // with strokes on the clipboard, a pen held still where it went down opens Paste here; the host drops its line
      await page.keyboard.press("Control+a");
      await until(async () => /^Lasso/.test(await page.locator(".pdfInkBar .inkToolBtn.modeActive").getAttribute("aria-label")),
        { what: "select all arms the lasso" });
      await page.keyboard.press("Control+c");
      await page.getByText("Copied 3 strokes.", { exact: true }).first().waitFor();
      await page.keyboard.press("1");
      await shell.down(83, 400, 650);
      before = await savedStrokes(paper.id);
      const still = Array.from({ length: 10 }, (_, i) => [400 + (i % 2) * 0.5, 650, 0.5, 70 * i, 50, 90]);
      assertEq(JSON.stringify(await shell.hold(83, still)), JSON.stringify({ cancel: true }), "the page takes the hold");
      await page.locator(".inkPasteMenu").getByText("Paste here", { exact: true }).waitFor();
      assertEq(JSON.stringify(await shell.stroke(83, [])), JSON.stringify({ committed: false }), "the lift after a cancel commits nothing");
      await page.keyboard.press("Escape");
      assertEq((await savedStrokes(paper.id)).size, before.size, "nothing saved");
      assertNoProblems(page);
    } finally { await ctx?.close(); }
  });

  await step("ipad shell: with the ruler on, a claim that starts by its edge carries snap, and the host's wandering samples commit as the projected line", async () => {
    let ctx;
    try {
      const opened = await shellPaper("ruler");
      ctx = opened.ctx;
      const { page, paper, shell } = opened;
      await page.keyboard.press("r");
      await page.waitForSelector('[data-page="1"] .inkRuler .inkRulerBand');   // the band: the ruler's own box has no height
      const ruler = await page.locator('[data-page="1"] .inkRuler').evaluate((el) =>
        ({ x: Number(el.dataset.x), y: Number(el.dataset.y), angle: Number(el.dataset.angle), length: Number(el.dataset.length) }));
      assertEq(ruler.angle, 0, "level");
      const g = await shell.geometry();
      // css px from the page's corner: the top edge is half the band (18 px) above the middle line
      const edge = ruler.y * g.k - 18, x0 = (ruler.x - ruler.length / 4) * g.k;
      // the armed pen, 5 px outside the top edge
      const claim = await shell.down(95, x0, edge - 5);
      const snap = claim.tool.snap;
      assert(snap, `the claim carries the edge: ${JSON.stringify(claim.tool)}`);
      assert(Math.abs(snap.x0 - (g.left + ruler.x * g.k)) < 0.02 && Math.abs(snap.y0 - (g.top + edge)) < 0.02 && snap.angle === 0,
        `snap at the edge's middle in viewport css px, the ruler's angle: ${JSON.stringify(snap)}`);
      assertEq(JSON.stringify(await shell.hold(95, [[x0, edge - 5, 0.5, 0], [x0 + 1, edge - 5, 0.5, 700]])), JSON.stringify({}),
        "along the ruler a hold answers nothing");
      const before = await savedStrokes(paper.id);
      const rows = Array.from({ length: 10 }, (_, i) => [x0 + 15 * i, edge - 5 + (i % 2 ? 8 : 0), 0.5, 16 * i]);
      assertEq(JSON.stringify(await shell.stroke(95, rows)), JSON.stringify({ committed: true }));
      const [line] = await newStrokes(paper.id, before, 1, "the ruler line saved");
      assertEq(JSON.stringify([line.ch, line.pen, line.shape?.kind]), JSON.stringify(["xyt", false, "line"]), "an even line with its line record");
      const p = line.shape.params, y = edge / g.k;
      assert(Math.abs(p.y1 - y) < 0.02 && Math.abs(p.y2 - y) < 0.02, `both ends on the edge: ${JSON.stringify(p)}`);
      assert(Math.abs(p.x1 - x0 / g.k) < 0.02 && Math.abs(p.x2 - (x0 + 135) / g.k) < 0.02, `from the first sample to the farthest: ${JSON.stringify(p)}`);
      assertNoProblems(page);
    } finally { await ctx?.close(); }
  });

  await step("ipad shell: under a shell without hold nothing answers it, and a held stroke commits freehand", async () => {
    let ctx;
    try {
      const opened = await shellPaper("older", { hold: false });
      ctx = opened.ctx;
      const { page, paper, shell } = opened;
      await shell.down(85, 100, 300);
      const before = await savedStrokes(paper.id);
      const line = heldLine(300);
      assertEq(await shell.hold(85, line), undefined, "no hold listener: the host reads the nothing as {}");
      assertEq(JSON.stringify(await shell.stroke(85, line)), JSON.stringify({ committed: true }));
      const [st] = await newStrokes(paper.id, before, 1, "the stroke saved");
      assert(!st.shape && st.ch === "xyptazw", `never recognised under it (${st.ch})`);
      assertNoProblems(page);
    } finally { await ctx?.close(); }
  });

  await step("ipad shell: View › Server bar hides the shell's own bar and brings it back, its check following the shell's word; upright, More lists it as the shell last left it; a shell without shellBar gets no row", async () => {
    const contexts = [];
    const open = async (options, viewport) => {
      const ctx = await alice.context(browser, { hasTouch: true, viewport, userAgent: SHELL_UA });
      contexts.push(ctx);
      await ctx.addInitScript(fakeShell, options);
      const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
      await page.waitForSelector(".folderNewBtn");
      const sent = () => page.evaluate(() => window.e2eHostSent.filter((m) => m.type === "shellBar"));
      return { page, sent };
    };
    const LANDSCAPE = { width: 1194, height: 834 };
    try {
      const { page, sent } = await open({}, LANDSCAPE);
      await until(async () => (await sent()).length > 0, { what: "the page asks how the bar stands" });
      assertEq(JSON.stringify((await sent())[0]), JSON.stringify({ type: "shellBar" }), "asked, not set");
      const view = page.getByRole("button", { name: "View", exact: true });
      const row = page.locator(".menuPopover").getByRole("menuitemcheckbox", { name: "Server bar", exact: true });
      await view.click();
      assertEq(await row.getAttribute("aria-checked"), "true", "the shell's bar is up");
      await row.click();
      assertEq(JSON.stringify((await sent()).at(-1)), JSON.stringify({ type: "shellBar", shown: false }), "the row hides it");
      await until(async () => (await row.getAttribute("aria-checked")) === "false", { what: "the row unchecked" });
      // The bar's own button or ⌘⇧B, in the shell: it tells the page.
      await page.evaluate(() => window.gammaNative.receive({ type: "shellBar", shown: true }));
      await until(async () => (await row.getAttribute("aria-checked")) === "true", { what: "the shell's word checks it" });
      await view.click();
      assertNoProblems(page);

      // Upright, the View menu's rows are More's; a bar the shell left
      // hidden comes unchecked.
      const upright = await open({ bar: false }, { width: 834, height: 1194 });
      await upright.page.waitForSelector(".app.phoneUI");
      await upright.page.getByRole("button", { name: "More", exact: true }).tap();
      const more = upright.page.locator(".userPopover").getByRole("menuitemcheckbox", { name: "Server bar", exact: true });
      await more.waitFor();
      assertEq(await more.getAttribute("aria-checked"), "false", "hidden, as the shell said");
      await more.tap();
      assertEq(JSON.stringify((await upright.sent()).at(-1)), JSON.stringify({ type: "shellBar", shown: true }), "the row brings it back");
      assertNoProblems(upright.page);

      // A shell that cannot hide its bar: no row, and nothing asked.
      const older = await open({ shellBar: false }, LANDSCAPE);
      await older.page.getByRole("button", { name: "View", exact: true }).click();
      await older.page.locator(".menuPopover").waitFor();
      assertEq(await older.page.locator(".menuPopover").getByRole("menuitemcheckbox", { name: "Server bar", exact: true }).count(), 0, "no row");
      assertEq((await older.sent()).length, 0, "nothing asked");
      assertNoProblems(older.page);
    } finally { for (const ctx of contexts) await ctx.close(); }
  });

  await step("ipad shell: Apple Pencil settings under the host; a double tap set to Undo undoes; Eraser while held arms the eraser and gives the tool back, short squeezes toggle it either way, an unknown phase does nothing, an erased selection stays gone; gestures wait for a claim; switches tick unless Haptics is off", async () => {
    let ctx;
    try {
      const opened = await shellPaper("gestures");
      ctx = opened.ctx;
      const { page, shell } = opened;
      const armed = () => page.locator(".pdfInkBar .inkToolBtn.modeActive").getAttribute("aria-label");
      const eraserArmed = () => page.locator(".pdfInkBar button[title^='Eraser'].modeActive").count();
      const pen = await armed();
      const row = (name) => page.locator(`.settingsPane [data-setting="${name}"]`).first();
      const openSettings = async () => {
        await page.getByRole("button", { name: "Account & settings", exact: true }).click();
        await page.getByRole("menuitem", { name: "Settings…", exact: true }).click();
        const dialog = page.getByRole("dialog", { name: "Settings", exact: true });
        await dialog.waitFor();
        await page.getByRole("navigation", { name: "Settings categories" }).getByRole("button", { name: "Reading & editing", exact: true }).click();
        await row("Apple Pencil").waitFor();
        return dialog;
      };
      const closeSettings = async (dialog) => {
        await page.getByRole("button", { name: "Close settings", exact: true }).click();
        await dialog.waitFor({ state: "hidden" });
      };
      const choose = async (setting, option) => {
        await row(setting).getByRole("button", { name: setting, exact: true }).click();
        await page.locator(".uiSelectMenu .ctxMenuItem", { hasText: option }).click();
      };
      let dialog = await openSettings();
      assertEq(await page.locator('.settingsPane .setSection[data-setting="Apple Pencil"] .setScope').getAttribute("data-scope"), "browser",
        "the Apple Pencil choices stay with this device");
      for (const name of ["Double tap", "Squeeze", "Haptics"]) assert(await row(name).count(), `the row "${name}"`);
      await choose("Double tap", "Undo");
      await row("Squeeze").getByRole("button", { name: "Squeeze", exact: true }).click();
      const offered = await page.locator(".uiSelectMenu .ctxMenuItem").allInnerTexts();
      assertEq(JSON.stringify(offered), JSON.stringify(["System setting", "Switch to eraser", "Switch to last tool", "Show tool options", "Undo",
        "Nothing", "Eraser while held", "Last tool while held"]), "the squeeze's choices, the held ones under a shell with phases");
      await page.locator(".uiSelectMenu .ctxMenuItem", { hasText: "Eraser while held" }).click();
      await row("Pressure-sensitive strokes").locator("input[type=checkbox]").uncheck();
      assertEq(JSON.stringify(await page.evaluate(() => ["gamma-ink-pencil-tap", "gamma-ink-pencil-squeeze"].map((k) => localStorage.getItem(k)))),
        JSON.stringify(["undo", "holdEraser"]), "browser preferences");
      await closeSettings(dialog);

      // a stroke the host hands back; a double tap set to Undo takes it back, but not while a claim is open
      const count = () => page.locator('[data-page="1"] .inkLayer path').count();
      const n0 = await count();
      const claim = await shell.down(91, 100, 250);
      assertEq(claim.tool.pressure, false, "Pressure-sensitive strokes off: the claim says the stroke reads none");
      await shell.stroke(91, [[100, 250, 0.5, 0], [150, 250, 0.5, 40], [200, 250, 0.5, 80]]);
      await until(async () => (await count()) === n0 + 1, { what: "the stroke drawn" });
      await shell.down(92, 300, 250);
      await shell.receive({ type: "pencil", gesture: "tap", action: "switchEraser" });
      await sleep(300);
      assertEq(await count(), n0 + 1, "a double tap under the moving pen undoes nothing");
      await shell.stroke(92, []);
      await shell.receive({ type: "pencil", gesture: "tap", action: "switchEraser" });
      await until(async () => (await count()) === n0, { what: "the double tap set to Undo undoes the stroke" });
      assertEq(await armed(), pen, "Undo switches no tool");

      // Eraser while held: the squeeze's start arms the eraser, its end gives the pen back
      let ticks = (await shell.sent("haptic")).length;
      await shell.receive({ type: "squeeze", phase: "began", action: "switchEraser" });
      await until(eraserArmed, { what: "the squeeze arms the eraser" });
      await sleep(350);
      await shell.receive({ type: "pencil", gesture: "squeeze", action: "switchEraser", phase: "ended" });
      await until(async () => (await armed()) === pen, { what: "its end gives the pen back" });
      const tools = (await shell.sent("haptic")).slice(ticks);
      assertEq(JSON.stringify(tools), JSON.stringify([{ type: "haptic", kind: "tool" }, { type: "haptic", kind: "tool" }]), "each switch ticks, with no place");
      // a cancel gives it back too
      await shell.receive({ type: "squeeze", phase: "began", action: "switchEraser" });
      await until(eraserArmed, { what: "armed again" });
      await sleep(350);
      await shell.receive({ type: "squeeze", phase: "cancelled", action: "switchEraser" });
      await until(async () => (await armed()) === pen, { what: "the cancel gives the pen back" });
      // a short squeeze that erased nothing is a toggle: the eraser stays
      await page.evaluate(() => {
        window.gammaNative.receive({ type: "squeeze", phase: "began", action: "switchEraser" });
        window.gammaNative.receive({ type: "pencil", gesture: "squeeze", action: "switchEraser", phase: "ended" });
      });
      await until(eraserArmed, { what: "the short squeeze arms the eraser" });
      await sleep(200);
      assertEq(await eraserArmed(), 1, "and it stays armed");
      // with the eraser armed, a held squeeze holds the pen and comes back, and a short one gives the pen back
      await shell.receive({ type: "squeeze", phase: "began", action: "switchEraser" });
      await until(async () => (await armed()) === pen, { what: "the pen while the squeeze is held" });
      await sleep(350);
      await shell.receive({ type: "pencil", gesture: "squeeze", action: "switchEraser", phase: "ended" });
      await until(eraserArmed, { what: "the eraser again at its end" });
      await page.evaluate(() => {
        window.gammaNative.receive({ type: "squeeze", phase: "began", action: "switchEraser" });
        window.gammaNative.receive({ type: "pencil", gesture: "squeeze", action: "switchEraser", phase: "ended" });
      });
      await until(async () => (await armed()) === pen, { what: "a second short squeeze gives the pen back" });
      await sleep(200);
      assertEq(await armed(), pen, "and it stays");
      // a squeeze in a phase the page does not know, or a `squeeze` with none, does nothing
      ticks = (await shell.sent("haptic")).length;
      await shell.receive({ type: "squeeze", phase: "changed", action: "switchEraser" });
      await shell.receive({ type: "pencil", gesture: "squeeze", action: "switchEraser", phase: "changed" });
      await shell.receive({ type: "squeeze", action: "switchEraser" });
      await sleep(200);
      assertEq(await armed(), pen, "no tool switched");
      assertEq((await shell.sent("haptic")).length, ticks, "and nothing ticked");

      // a pen lasso that selects ticks where it lifts
      await shell.down(94, 100, 650);
      await shell.stroke(94, [[100, 650, 0.5, 0], [150, 650, 0.5, 40], [200, 650, 0.5, 80]]);
      await until(async () => (await count()) === n0 + 1, { what: "a stroke to select" });
      await page.keyboard.press("l");
      await until(async () => /^Lasso/.test(await armed()), { what: "the lasso armed" });
      const lasso = await armed();
      ticks = (await shell.sent("haptic")).length;
      await shell.pdf.evaluate((el) => {
        const box = el.getBoundingClientRect();
        const send = (type, x, y) => el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerType: "pen", pointerId: 95,
          button: 0, buttons: type === "pointerup" ? 0 : 1, pressure: 0.5, clientX: box.left + x, clientY: box.top + y }));
        send("pointerdown", 60, 600);
        for (const [x, y] of [[260, 600], [260, 700], [60, 700], [60, 610]]) send("pointermove", x, y);
        send("pointerup", 60, 610);
      });
      const select = await until(async () => (await shell.sent("haptic")).slice(ticks).find((m) => m.kind === "select"),
        { what: "the lasso's selection ticks" });
      const [lift] = await shell.toViewport([[60, 610]]);
      assert(Math.abs(select.at.x - lift[0]) < 0.011 && Math.abs(select.at.y - lift[1]) < 0.011, `at its lift (${JSON.stringify(select)})`);
      const selected = () => page.locator('[data-page="1"] [data-ink-selection]').count();
      await until(async () => (await selected()) === 1, { what: "the stroke selected" });
      // a held squeeze puts the selection aside with the lasso and brings both back
      await shell.receive({ type: "squeeze", phase: "began", action: "switchEraser" });
      await until(async () => (await eraserArmed()) && !(await selected()), { what: "the eraser armed, the selection gone with the lasso" });
      await sleep(350);
      await shell.receive({ type: "pencil", gesture: "squeeze", action: "switchEraser", phase: "ended" });
      await until(async () => (await armed()) === lasso && (await selected()) === 1, { what: "the lasso back with its selection" });
      // a held squeeze that erases the selected stroke brings the lasso back with nothing selected
      await shell.receive({ type: "squeeze", phase: "began", action: "switchEraser" });
      await until(async () => (await eraserArmed()) && !(await selected()), { what: "the eraser armed again" });
      await shell.pdf.evaluate((el) => {
        const box = el.getBoundingClientRect();
        const send = (type, x, y) => el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerType: "pen", pointerId: 96,
          button: 0, buttons: type === "pointerup" ? 0 : 1, pressure: 0.5, clientX: box.left + x, clientY: box.top + y }));
        send("pointerdown", 150, 620);
        for (const y of [630, 640, 650, 660, 670, 680]) send("pointermove", 150, y);
        send("pointerup", 150, 680);
      });
      await until(async () => (await count()) === n0, { what: "the selected stroke erased" });
      await sleep(350);
      await shell.receive({ type: "pencil", gesture: "squeeze", action: "switchEraser", phase: "ended" });
      await until(async () => (await armed()) === lasso, { what: "the lasso back" });
      assertEq(await selected(), 0, "nothing selected");
      // nothing to deselect: Escape closes the strip at once, as it does with no selection
      await page.keyboard.press("Escape");
      await until(async () => !(await page.$(".pdfInkBar")), { what: "Escape closes the strip: no selection was left behind" });
      await page.click("button[aria-label='Handwriting tools']");
      await page.waitForSelector(".pdfInkBar .inkToolBtn.modeActive");

      // Haptics off: switches no longer tick
      dialog = await openSettings();
      await row("Haptics").locator("input[type=checkbox]").uncheck();
      await closeSettings(dialog);
      ticks = (await shell.sent("haptic")).length;
      await page.keyboard.press("1");
      await shell.receive({ type: "squeeze", phase: "began", action: "switchEraser" });
      await until(eraserArmed, { what: "the eraser armed" });
      await sleep(350);
      await shell.receive({ type: "pencil", gesture: "squeeze", action: "switchEraser", phase: "ended" });
      await until(async () => (await armed()) === pen, { what: "the pen back" });
      assertEq((await shell.sent("haptic")).length, ticks, "no tick with Haptics off");
      assertNoProblems(page);
    } finally { await ctx?.close(); }
  });

  await step("phone: a labelled bottom bar, the More sheet, no empty topbar, the Library tab as home", async () => {
    await alice.api("/api/prefs/open-tabs", { method: "PUT", body: { value: [] } });
    const note = await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "A phone title long enough to need a second line on a narrow screen" } });
    await alice.file(note.id, { labels: ["phonelabel"] });
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
      await sheet.getByRole("menuitem", { name: "Import…", exact: true }).waitFor();
      await sheet.getByRole("menuitem", { name: "Settings…", exact: true }).waitFor();
      await sheet.getByRole("menuitem", { name: "Background tasks", exact: true }).tap();
      await page.locator(".tasksPopover").waitFor();
      assertEq(await sheet.count(), 0, "the sheet gave way to the tasks");
      await page.keyboard.press("Escape");
      await page.locator(".tasksPopover").waitFor({ state: "detached" });
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

  // The editing bar (docs/dev/ipad.md "The editing bar"): on a touch screen
  // an open editor brings a strip of the keyboard's commands; each tap runs
  // its catalog command and the editor keeps the focus (the iPad's keyboard
  // stays up only while it does). Emulation has no on-screen keyboard: a
  // stand-in visual viewport plays one (shrunk by the keyboard, scrolled as
  // iPadOS scrolls it, its events late or missing) and the bar must land on
  // it; the device check is in ipad.md.
  await step("ipad: the editing bar indents, moves, adds, formats and undoes by tap, keeping the editor", async () => {
    const note = await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Bar page" } });
    const ids = {};
    for (const text of ["First idea", "Second idea", "Third idea"]) {
      ids[text] = (await alice.api("/api/blocks", { method: "POST", body: { parent_id: note.id, content: text } })).id;
    }
    const outline = async () => (await tree(alice, note.id)).map((b) => [b.content, ...b.children.map((c) => c.content)]);
    // the three in the page's order, whatever order the API gave them
    const [one, two, three] = (await outline()).map(([text]) => text);
    const ctx = await alice.context(browser, { hasTouch: true, deviceScaleFactor: 2, viewport: { width: 834, height: 1194 } });
    await ctx.addInitScript(() => {
      const real = window.visualViewport;
      const fake = new EventTarget();
      let keyboard = 0, scrolled = 0;
      for (const k of ["width", "offsetLeft", "pageLeft", "scale"]) Object.defineProperty(fake, k, { get: () => real[k] });
      Object.defineProperty(fake, "height", { get: () => real.height - keyboard });
      Object.defineProperty(fake, "offsetTop", { get: () => real.offsetTop + scrolled });
      Object.defineProperty(fake, "pageTop", { get: () => real.pageTop + scrolled });
      Object.defineProperty(window, "visualViewport", { get: () => fake, configurable: true });
      // A keyboard `px` tall, the visual viewport scrolled `by` into the
      // layout viewport; `event` names the event sent, if any, BEFORE the
      // values change (iPadOS's come as the keyboard starts to move).
      window.e2eKeyboard = (px, by = 0, event = "resize") => {
        if (event) fake.dispatchEvent(new Event(event));
        keyboard = px; scrolled = by;
      };
    });
    try {
      const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}&block=${note.id}`);
      const bar = page.locator(".editBar");
      // The bar shows with its bottom edge `gap` above `y` (and centred).
      const barOn = async (y, what) => until(async () => {
        const b = await bar.boundingBox();
        return Math.abs(b.y + b.height - (y - 6)) <= 1 && Math.abs(b.x + b.width / 2 - 417) <= 1
          && !(await bar.evaluate((el) => el.classList.contains("moving")));
      }, { what });
      const tool = (name) => bar.getByRole("button", { name, exact: true });
      const editing = (id) => page.evaluate((id) => !!document.activeElement?.closest(`.blockEditorCm[data-block-id="${id}"]`), id);
      const keptOn = async (id, what) => until(() => editing(id), { what: `${what}: the editor keeps the focus` });
      // The new block's text as saved (the editor shows marks and math rendered).
      const saved = async (text) => until(async () => (await tree(alice, note.id))[3]?.content === text, { what: `the new block reads ${text}` });

      await page.locator(".blockRow", { hasText: two }).first().locator(".blockBody").tap();
      await bar.waitFor();
      await keptOn(ids[two], "a tap opened it");
      // With no keyboard up it floats above the compact shell's bottom bar,
      // which stays tappable.
      const tabs = await page.locator(".phoneBottomBar").boundingBox();
      await barOn(tabs.y, "the bar floats above the bottom bar");
      // The keyboard comes up: the bar follows it there, though its event
      // came before the move.
      await page.evaluate(() => window.e2eKeyboard(320));
      await barOn(1194 - 320, "the bar rides on the keyboard");
      // iPadOS scrolls the visual viewport into the layout viewport.
      await page.evaluate(() => window.e2eKeyboard(320, 140, "scroll"));
      await barOn(140 + 1194 - 320, "the bar stays on the keyboard when the view scrolls");
      // A pan that goes on, a frame at a time: the bar stays out of sight
      // the whole way (never chasing it), then shows where the view stops.
      const shownWhilePanning = await page.evaluate(() => new Promise((resolve) => {
        const el = document.querySelector(".editBar");
        let i = 0, shown = 0;
        const tick = () => {
          window.e2eKeyboard(320, 140 + i * 3, "scroll");
          if (i > 2 && !el.classList.contains("moving")) shown++;
          if (++i < 40) requestAnimationFrame(tick); else resolve(shown);
        };
        requestAnimationFrame(tick);
      }));
      assertEq(shownWhilePanning, 0, "frames the bar showed while the view panned");
      await barOn(140 + 39 * 3 + 1194 - 320, "the bar shows where the pan stopped");
      // The keyboard's own hide key, then back with no event at all, for an
      // editor that kept the focus: the next tap finds it.
      await page.evaluate(() => window.e2eKeyboard(0));
      await barOn(tabs.y, "the bar goes back above the bottom bar");
      await page.evaluate(() => window.e2eKeyboard(300, 0, null));
      await page.locator(".blockEditorCm .cm-content").tap();
      await barOn(1194 - 300, "a tap finds the keyboard back");
      await page.evaluate(() => window.e2eKeyboard(0));
      await barOn(tabs.y, "and gone again");
      assert((await tool("Indent block").boundingBox()).width >= 36, "finger-sized buttons");
      assert(await tool("Outdent block").isDisabled(), "a top-level block has nothing to outdent from");

      await tool("Indent block").tap();
      await until(async () => same(await outline(), [[one, two], [three]]), { what: "Indent nests it under the block above" });
      await keptOn(ids[two], "Indent");
      assert(await tool("Indent block").isDisabled(), "a first child has no block above to go under");
      await tool("Outdent block").tap();
      await until(async () => same(await outline(), [[one], [two], [three]]), { what: "Outdent lifts it back" });
      await keptOn(ids[two], "Outdent");
      await tool("Move block down").tap();
      await until(async () => same(await outline(), [[one], [three], [two]]), { what: "Move down swaps it with the next" });
      await keptOn(ids[two], "Move down");
      assert(await tool("Move block down").isDisabled(), "the last block cannot go further down");

      // New block: an empty block after it, its editor focused; typing goes there.
      await tool("New block below").tap();
      await until(async () => (await outline()).length === 4, { what: "New block adds one after it" });
      const fresh = await until(() => page.evaluate(() => {
        const ed = document.activeElement?.closest(".blockEditorCm");
        return ed?.querySelector(".cm-placeholder") ? ed.dataset.blockId : null;
      }), { what: "the new, empty block's editor has the focus" });
      assert(!Object.values(ids).includes(fresh), "the focus is on the new block");
      await page.keyboard.type("Fourth");
      await saved("Fourth");
      // past the history's typing run (blockHistory.js TYPING_MERGE_MS), as a
      // finger selecting the word is: Bold is then an undo step of its own
      await sleep(600);
      await page.keyboard.press("Shift+Home");
      await tool("Bold").tap();
      await saved("**Fourth**");
      await keptOn(fresh, "Bold");
      await tool("Undo").tap();
      await saved("Fourth");
      await keptOn(fresh, "Undo");
      await tool("Redo").tap();
      await saved("**Fourth**");
      // The iPad keyboard's own undo / redo keys send these input events;
      // the editor hands them to the same history.
      const keyboardHistory = (inputType) => page.evaluate((inputType) => {
        document.activeElement.dispatchEvent(new InputEvent("beforeinput", { inputType, bubbles: true, cancelable: true }));
      }, inputType);
      await keyboardHistory("historyUndo");
      await saved("Fourth");
      await keyboardHistory("historyRedo");
      await saved("**Fourth**");
      await keptOn(fresh, "the keyboard's undo and redo");
      await page.keyboard.press("End");
      await tool("Inline equation").tap();
      await saved("**Fourth**$x$");
      await page.keyboard.type("y"); // the x is selected: typing replaces it
      await saved("**Fourth**$y$");

      // Insert: the slash menu, without typing "/".
      await page.keyboard.press("End");
      await tool("Insert…").tap();
      await page.locator(".slashMenu").waitFor();
      await page.locator(".slashMenuItem", { hasText: "Heading 1" }).first().tap();
      // (the space Insert put before the "/", as typing it would, stays)
      await until(async () => (await tree(alice, note.id))[3]?.content.trimEnd() === "# **Fourth**$y$", { what: "a tapped menu item applies" });

      // Done: the editor closes and the bar goes with it.
      await bar.getByRole("button", { name: "Done", exact: true }).tap();
      await page.waitForSelector(".blockEditorCm", { state: "detached", timeout: 5000 });
      assertEq(await bar.count(), 0, "the bar leaves with the editor");
      assertNoProblems(page);
    } finally { await ctx.close(); }

    // A mouse on a desktop: no bar.
    const desk = await alice.context(browser);
    try {
      const page = await openPage(desk, `${server.base}/?ws=${alice.ws}&block=${note.id}`);
      await page.locator(".blockRow", { hasText: one }).first().locator(".blockBody").click();
      await page.waitForSelector(".blockEditorCm .cm-content");
      assertEq(await page.locator(".editBar").count(), 0, "no bar with a mouse");
      assertNoProblems(page);
    } finally { await desk.close(); }
  });

  // The bar is the notes' one, not each row's: from one open editor to the
  // next it stays where it is, never out of sight, with its tools for the
  // block now open; it starts out of sight only for the first editor, the
  // keyboard's rise.
  await step("ipad: the editing bar stays up from one editor to the next: a new block, an indent and an outdent by key or by tap, and another note tapped, with its tools for the open block", async () => {
    const note = await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Steady bar page" } });
    for (const text of ["Alpha idea", "Beta idea"]) await alice.api("/api/blocks", { method: "POST", body: { parent_id: note.id, content: text } });
    const [first, second] = (await tree(alice, note.id)).map((b) => b.content);
    const ctx = await alice.context(browser, { hasTouch: true, isMobile: true, deviceScaleFactor: 2, viewport: { width: 1194, height: 834 } });
    try {
      const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}&block=${note.id}`);
      const bar = page.locator(".editBar");
      const tool = (name) => bar.getByRole("button", { name, exact: true });
      // Every frame from the action on, for 700 ms: the frames the bar was
      // missing, out of sight or fading in, and the block whose editor is open.
      const watch = (act) => page.evaluate(() => {
        window.e2eBarFrames = { missed: 0, frames: 0 };
        const start = performance.now();
        const tick = () => {
          const el = document.querySelector(".editBar");
          const w = window.e2eBarFrames;
          w.frames++;
          if (!el || el.classList.contains("moving") || Number(getComputedStyle(el).opacity) < 0.99) w.missed++;
          if (performance.now() - start < 700) requestAnimationFrame(tick); else w.done = true;
        };
        requestAnimationFrame(tick);
      }).then(act).then(() => until(() => page.evaluate(() => window.e2eBarFrames.done && window.e2eBarFrames), { what: "700 ms watched" }));
      const open = () => page.evaluate(() => document.activeElement?.closest(".blockEditorCm")?.dataset.blockId || null);
      await page.locator(".blockRow", { hasText: first }).first().locator(".blockBody").tap();
      await until(() => bar.evaluate((el) => !el.classList.contains("moving") && Number(getComputedStyle(el).opacity) >= 0.99).catch(() => false),
        { what: "the bar shows for the first editor, once the keyboard's rise is over" });
      await page.keyboard.press("End");
      for (const [what, act] of [
        ["Shift+Enter, a new block", () => page.keyboard.press("Shift+Enter")],
        ["Tab, an indent", () => page.keyboard.press("Tab")],
        ["Shift+Tab, an outdent", () => page.keyboard.press("Shift+Tab")],
        ["the bar's New block below", () => tool("New block below").tap()],
        ["the bar's Indent", () => tool("Indent block").tap()],
        ["the bar's Outdent", () => tool("Outdent block").tap()],
      ]) {
        const seen = await watch(act);
        assertEq(seen.missed, 0, `frames the bar was out of sight through ${what} (of ${seen.frames})`);
      }
      // The tools are the open block's: indented, it can be outdented and
      // not indented again; at the top level, the reverse.
      const fresh = await open();
      assert(fresh, "a new block's editor is open");
      await tool("Indent block").tap();
      await until(async () => !(await tool("Outdent block").isDisabled()) && (await tool("Indent block").isDisabled()), { what: "indented, the bar offers Outdent" });
      assertEq(await open(), fresh, "the indented block's editor keeps the focus");
      await tool("Outdent block").tap();
      await until(async () => (await tool("Outdent block").isDisabled()), { what: "back at the top, nothing to outdent" });
      const other = await watch(() => page.locator(".blockRow", { hasText: second }).first().locator(".blockBody").tap());
      assertEq(other.missed, 0, `frames the bar was out of sight as another note opened (of ${other.frames})`);
      await until(async () => (await page.evaluate(() => document.activeElement?.closest(".blockRow")?.textContent || "")).includes(second), { what: "the other note's editor is open" });
      await tool("Done").tap();
      await page.waitForSelector(".blockEditorCm", { state: "detached", timeout: 5000 });
      assertEq(await bar.count(), 0, "the bar leaves with the last editor");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });
}
