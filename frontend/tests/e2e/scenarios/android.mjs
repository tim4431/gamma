// The Android app's shell over the web app (docs/dev/ipad.md "The host
// contract"; docs/dev/android.md): the app's own document-start script
// (android/app/src/main/assets/shell/host.js) runs as the app injects it,
// over a stand-in for its message port that carries JSON strings both ways
// (../shells.mjs). It covers what the Android host changes for the page:
// the host found and spoken to through its script's `post`, the theme, the
// layout by orientation; the stylus's claim and stroke over the port, a
// declined claim left to the page, the hold and its tick, the pen's two
// buttons; the Stylus settings and their search, gated by the gestures the
// host forwards, and no Lock angle without a barrel roll; the saves the app
// takes (a blob in chunks, a URL by itself, a backup's under the server's
// name) and a refused one, with no
// browser download; a file the app was given; the shell's bar; no folder
// picker; the server inside the app on Settings › Server; Gamma Cloud in
// Account & sync with no Open account and a link to the page on deleting
// the account, and the privacy policy under Help; and the chrome
// pages (desktop/ui) under the app's chrome.js, beside the iPad's and the
// desktop's. Chromium in a tablet-sized touch context, emulation, not a
// device.
import fs from "node:fs";
import path from "node:path";
import { Account, ROOT } from "../harness.mjs";
import { decodeStroke } from "../../../src/ink/ink.js";
import { newInkStrokes, savedInkStrokes } from "./ink.mjs";
import { waitForPdf } from "./pdf.mjs";
import { ANDROID_UA, T_SNAP, androidShell, heldLine, shellPaper } from "../shells.mjs";

const LANDSCAPE = { width: 1280, height: 800 };
// A save as the stand-in kept it: its chunks decoded one by one.
const savedBytes = (save) => Buffer.concat(save.parts.map((part) => Buffer.from(part, "base64")));

export async function androidScenarios(env) {
  const { server, browser, alice, makePdf, step, until, sleep, assert, assertEq, assertNoProblems, openPage } = env;
  const paperOn = (name, flags) => shellPaper(env, "android", name, flags);
  // An Android context on a page: the shell's stand-in with `options`, the page open.
  const androidPage = async (url, options = {}, viewport = LANDSCAPE) => {
    const ctx = await alice.context(browser, { hasTouch: true, viewport, userAgent: ANDROID_UA });
    await androidShell(ctx, options);
    const page = await openPage(ctx, url);
    const sent = (type) => page.evaluate((type) => window.e2eHostSent.filter((m) => m.type === type), type);
    const send = (msg) => page.evaluate((msg) => window.e2eSend(msg), msg);
    return { ctx, page, sent, send };
  };
  const savedStrokes = (paperId) => savedInkStrokes(alice, paperId);
  const newStrokes = (paperId, before, n, what) => newInkStrokes(alice, paperId, before, n, what);
  // A page with a PDF (makePdf's, with `pdfOptions`), made through the API; the PDF's bytes.
  const pdfPage = async (title, text, pdfOptions) => {
    const bytes = makePdf([[text]], pdfOptions);
    const up = await alice.upload("/api/uploads", bytes, `${title}.pdf`, "application/pdf");
    const paper = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: title, source_url: up.source_url } });
    return { paper, bytes };
  };

  await step("android shell: the app's script declares the host and carries the page's messages as JSON over its port; the theme, the tablet layout by orientation, no install offer", async () => {
    const { paper } = await pdfPage("Android shell paper", "A page the Android app shows.");
    const { ctx, page, sent } = await androidPage(`${server.base}/?ws=${alice.ws}&block=${paper.id}`);
    try {
      await waitForPdf(page, 1);
      assertEq(await page.locator("html").getAttribute("data-host"), "android");
      assertEq(await page.locator("html").getAttribute("data-shell"), "android", "the shell takes the full-screen rules");
      const host = await page.evaluate(() => ({ webkit: typeof window.webkit, hellos: window.e2eHellos, post: typeof window.gammaNative.post,
        platform: window.gammaNative.platform, save: window.gammaNative.save, gestures: window.gammaNative.pencilGestures,
        phases: "pencilPhases" in window.gammaNative, roll: "pencilRoll" in window.gammaNative }));
      assertEq(JSON.stringify(host), JSON.stringify({ webkit: "undefined", hellos: 1, post: "function", platform: "android", save: true,
        gestures: ["tap", "squeeze"], phases: false, roll: false }), "the host as the app declares it, no WebKit");
      // theme: the colour behind the web view, the scheme for the system bars, the theme's name for the chrome
      const theme = await until(async () => (await sent("theme")).at(-1), { what: "a theme message over the port" });
      assertEq(theme.bg, await page.locator('meta[name="theme-color"]').getAttribute("content"), "the window's colour is the status bar's");
      assertEq(theme.scheme, await page.locator("html").getAttribute("data-scheme"));
      assertEq(theme.name, await page.locator("html").getAttribute("data-theme"));
      assertEq(await page.getByText("Add to Home Screen").count(), 0, "no install offer");
      // a tablet: the docks in landscape, the compact shell upright
      assertEq(await page.locator(".app.phoneUI").count(), 0, "landscape keeps the docks");
      await page.setViewportSize({ width: 800, height: 1280 });
      await page.waitForSelector(".app.phoneUI");
      await page.setViewportSize(LANDSCAPE);
      await until(async () => (await page.locator(".app.phoneUI").count()) === 0, { what: "landscape again" });
      // a new document says hello again: native keeps its reply proxy
      await page.reload();
      await waitForPdf(page, 1);
      assertEq(await page.evaluate(() => window.e2eHellos), 1, "one hello per document");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("android shell: a stylus stroke is claimed and committed from the host's samples with their tilt; a cancelled touch saves nothing; the pen's button and its second button switch tools, ticking", async () => {
    let ctx;
    try {
      const opened = await paperOn("strokes");
      ctx = opened.ctx;
      const { page, paper, shell } = opened;
      const { left, top } = await shell.geometry();
      const claim = await shell.down(61, 100, 300);
      assertEq(JSON.stringify(claim.at), JSON.stringify({ x: left + 100, y: top + 300 }), "where the pen went down");
      assertEq(claim.tool.kind, "pen");
      const before = await savedStrokes(paper.id);
      const rows = Array.from({ length: 10 }, (_, i) => [100 + 10 * i, 300, 0.3 + 0.05 * i, 8 * i, 42, 120]);
      assertEq(JSON.stringify(await shell.stroke(61, rows)), JSON.stringify({ committed: true }), "committed, answered over the port");
      const [st] = await newStrokes(paper.id, before, 1, "the host's stroke saved");
      assertEq(st.ch, "xyptazw", "pressure, tilt and width");
      assert(decodeStroke(st).every((q) => q.a === 42 && q.z === 120), "the tilt in degrees on every sample");
      // the system cancelled a touch: no samples, nothing saved
      await shell.down(62, 200, 400);
      assertEq(JSON.stringify(await shell.stroke(62, [])), JSON.stringify({ committed: false }));
      assertEq((await savedStrokes(paper.id)).size, before.size + 1, "nothing saved for a cancelled touch");
      // the pen's button (`tap`, the app's switchEraser) toggles the eraser; its second button
      // (a squeeze with no phase, switchPrevious) goes to the last tool; each switch ticks
      const armed = () => page.locator(".pdfInkBar .inkToolBtn.modeActive").getAttribute("aria-label");
      const eraserArmed = () => page.locator(".pdfInkBar button[title^='Eraser'].modeActive").count();
      const pen = await armed();
      const ticks = (await shell.sent("haptic")).length;
      await shell.receive({ type: "pencil", gesture: "tap", action: "switchEraser" });
      await until(eraserArmed, { what: "the button arms the eraser" });
      await shell.receive({ type: "pencil", gesture: "tap", action: "switchEraser" });
      await until(async () => (await armed()) === pen, { what: "and again gives the pen back" });
      await shell.receive({ type: "pencil", gesture: "squeeze", action: "switchPrevious" });
      await until(eraserArmed, { what: "the second button goes to the last tool, the eraser" });
      await shell.receive({ type: "pencil", gesture: "squeeze", action: "switchPrevious" });
      await until(async () => (await armed()) === pen, { what: "and back to the pen" });
      assertEq(JSON.stringify((await shell.sent("haptic")).slice(ticks)), JSON.stringify(Array(4).fill({ type: "haptic", kind: "tool" })),
        "each switch ticks the device, with no place");
      assertNoProblems(page);
    } finally { await ctx?.close(); }
  });

  await step("android shell: a declined claim leaves the stroke to the page's own pointer events, which commit it", async () => {
    let ctx;
    try {
      const opened = await paperOn("declined", { claims: false });
      ctx = opened.ctx;
      const { page, paper, shell } = opened;
      const before = await savedStrokes(paper.id);
      const send = (type, x, buttons = 1) => shell.pdf.evaluate((el, [type, x, buttons]) => {
        const box = el.getBoundingClientRect();
        el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerType: "pen", pointerId: 71,
          button: 0, buttons, pressure: 0.5, clientX: box.left + x, clientY: box.top + 250 }));
      }, [type, x, buttons]);
      await send("pointerdown", 100);
      await send("pointermove", 120);
      const claim = await until(async () => (await shell.sent("claim")).find((m) => m.pointerId === 71), { what: "the stroke is claimed" });
      assertEq(claim.tool.kind, "pen");
      await sleep(200); // the answer has come back over the port
      assert((await shell.pdf.locator(".inkCanvas").evaluate((c) => c.style.display)) !== "none", "the page keeps drawing its draft");
      for (const x of [140, 160, 180, 200]) await send("pointermove", x);
      await send("pointerup", 200, 0);
      const [st] = await newStrokes(paper.id, before, 1, "the page's own stroke saved");
      assert(st.pen === true && decodeStroke(st).length >= 4, `a pen stroke from the page's events: ${st.ch}`);
      // the claim is over: a gesture acts at once
      await shell.receive({ type: "pencil", gesture: "tap", action: "switchEraser" });
      await page.waitForSelector(".pdfInkBar button[title^='Eraser'].modeActive");
      assertNoProblems(page);
    } finally { await ctx?.close(); }
  });

  await step("android shell: the host's hold snaps a still line in css px and ticks the device; the lift commits it", async () => {
    let ctx;
    try {
      const opened = await paperOn("holds");
      ctx = opened.ctx;
      const { page, paper, shell } = opened;
      const line = heldLine(300);
      await shell.down(81, 100, 300);
      const before = await savedStrokes(paper.id);
      const answer = await shell.hold(81, line);
      assertEq(answer?.shape?.kind, "line", `the hold answers a line (${JSON.stringify(answer)})`);
      const [from] = await shell.toViewport([line.at(-1)]);
      const snap = (await shell.sent("haptic")).at(-1);
      assert(snap?.kind === "snap" && Math.abs(snap.at.x - from[0]) < 0.011 && Math.abs(snap.at.y - from[1]) < 0.011,
        `a snap ticks at the hold's last sample (${JSON.stringify(snap)})`);
      const after = Array.from({ length: 10 }, (_, i) => [250 + 4 * (i + 1), 300 + 3 * (i + 1), 0.5, T_SNAP + 200 + 40 * i, 50, 90]);
      assertEq(JSON.stringify(await shell.stroke(81, [...line, ...after], line.length)), JSON.stringify({ committed: true }));
      const [held] = await newStrokes(paper.id, before, 1, "the held line saved");
      assertEq(JSON.stringify([held.shape?.kind, held.ch]), JSON.stringify(["line", "xyt"]), "a shape, at constant width");
      assertNoProblems(page);
    } finally { await ctx?.close(); }
  });

  await step("android shell: Settings shows a Stylus section with the pen's buttons, Default first, the device's Haptics; the search knows no squeeze; a host that forwards one gesture and plays no haptics shows its one row; no Lock angle without a barrel roll", async () => {
    const contexts = [];
    await alice.api("/api/prefs/profile", { method: "PATCH", body: { set: { inkTools: [
      { id: "cal1", kind: "pen", color: "#1f1f1f", size: 2, brush: "calligraphy" },
    ] } } });
    try {
      const opened = await paperOn("settings");
      contexts.push(opened.ctx);
      const { page } = opened;
      // a calligraphy pen's options row: the nib's angle, and no Lock angle, which needs the pen's roll
      await page.keyboard.press("1");
      await page.locator(".pdfInkBar .inkToolBtn.modeActive").first().click();
      await page.locator('[data-ink-options="tool"] .inkNibReadout').waitFor();
      assertEq(await page.locator('[data-ink-options="tool"] button[aria-label="Lock angle"]').count(), 0, "no Lock angle");
      await page.keyboard.press("Escape");

      const row = (p, name) => p.locator(`.settingsPane [data-setting="${name}"]`).first();
      const openSettings = async (p) => {
        await p.getByRole("button", { name: "Account & settings", exact: true }).click();
        await p.getByRole("menuitem", { name: "Settings…", exact: true }).click();
        const dialog = p.getByRole("dialog", { name: "Settings", exact: true });
        await dialog.waitFor();
        await p.getByRole("navigation", { name: "Settings categories" }).getByRole("button", { name: "Reading & editing", exact: true }).click();
        await row(p, "Hover preview").waitFor();
        return dialog;
      };
      await openSettings(page);
      const section = page.locator('.settingsPane .setSection[data-setting="Stylus"]');
      await section.waitFor();
      assertEq(await section.locator(".setScope").getAttribute("data-scope"), "browser", "the choices stay with this device");
      assertEq(await page.locator('.settingsPane [data-setting="Apple Pencil"]').count(), 0, "no Apple Pencil");
      for (const name of ["Pen button", "Second pen button", "Haptics"]) assert(await row(page, name).count(), `the row "${name}"`);
      for (const name of ["Double tap", "Squeeze"]) assertEq(await row(page, name).count(), 0, `no "${name}"`);
      assert((await row(page, "Haptics").innerText()).includes("A tick when a shape snaps, the lasso selects or the tool changes"),
        "the device's tick, not Apple Pencil Pro's");
      await row(page, "Pen button").getByRole("button", { name: "Pen button", exact: true }).click();
      assertEq(JSON.stringify(await page.locator(".uiSelectMenu .ctxMenuItem").allInnerTexts()),
        JSON.stringify(["Default", "Switch to eraser", "Switch to last tool", "Show tool options", "Undo", "Nothing"]),
        "Default first, no hold choices without phases");
      await page.locator(".uiSelectMenu .ctxMenuItem", { hasText: "Undo" }).click();
      assertEq(await page.evaluate(() => localStorage.getItem("gamma-ink-pencil-tap")), "undo", "a browser preference");
      // the search: the stylus's words, none of Apple's
      const search = page.getByRole("searchbox", { name: "Search settings" });
      await search.fill("squeeze");
      await sleep(150);
      assertEq(await page.locator(".settingsSearchResult").count(), 0, "nothing is a squeeze here");
      await search.fill("pen button");
      await page.locator(".settingsSearchResult").filter({ has: page.getByText("Second pen button", { exact: true }) }).waitFor();
      await search.fill("apple pencil");
      await sleep(150);
      assertEq(await page.locator(".settingsSearchResult").filter({ hasText: /Double tap|Squeeze/ }).count(), 0, "no Apple Pencil rows");
      assertNoProblems(page);

      // a host that forwards the button alone and has no vibrator
      const one = await androidPage(`${server.base}/?ws=${alice.ws}`, { pencilGestures: ["tap"], haptics: false });
      contexts.push(one.ctx);
      await one.page.waitForSelector(".folderNewBtn");
      await openSettings(one.page);
      await one.page.locator('.settingsPane .setSection[data-setting="Stylus"]').waitFor();
      assert(await row(one.page, "Pen button").count(), "the button's row");
      for (const name of ["Second pen button", "Haptics"]) assertEq(await row(one.page, name).count(), 0, `no "${name}"`);
      assertNoProblems(one.page);
    } finally {
      for (const ctx of contexts) await ctx.close();
      await alice.api("/api/prefs/profile", { method: "PATCH", body: { set: { inkTools: null } } }).catch(() => {});
    }
  });

  await step("android shell: blobs go to the app: Download the PDF in base64 chunks at their offsets, byte for byte, and the lasso's SVG; a refused save says so and sends no data; the browser downloads nothing", async () => {
    const contexts = [];
    try {
      // a PDF of 2.5 MB: two chunks, the first SAVE_CHUNK (2,097,150) bytes, a multiple of 3
      const { paper, bytes } = await pdfPage("Android saves", "A page the app saves.", { padBytes: 2_500_000 });
      const { ctx, page, sent } = await androidPage(`${server.base}/?ws=${alice.ws}&block=${paper.id}`);
      contexts.push(ctx);
      const downloads = [];
      page.on("download", (d) => downloads.push(d.suggestedFilename()));
      const saved = () => page.evaluate(() => window.e2eSaved);
      await waitForPdf(page, 1);
      // the port's stand-in keeps no chunk in e2eHostSent: watch them go by, as [offset, base64 length]
      await page.evaluate(() => {
        const port = window.gammaAndroidHost;
        const post = port.postMessage.bind(port);
        window.e2eChunks = [];
        port.postMessage = (data) => {
          const m = JSON.parse(data);
          if (m.msg?.type === "saveData") window.e2eChunks.push([m.msg.offset, m.msg.data.length]);
          return post(data);
        };
      });
      await page.locator('[data-guide="page.document"]').click();
      await page.getByRole("menuitem", { name: "Download the PDF", exact: true }).click();
      const [pdf] = await until(async () => ((await saved()).length ? saved() : null), { what: "the PDF handed to the app", timeout: 15000 });
      assertEq(JSON.stringify([pdf.name, pdf.mime, pdf.size]), JSON.stringify(["Android saves.pdf", "application/pdf", bytes.length]),
        "named by the page, typed, its size announced");
      assertEq(JSON.stringify(await page.evaluate(() => window.e2eChunks)), JSON.stringify([[0, 2097150 / 3 * 4], [2097150, Math.ceil((bytes.length - 2097150) / 3) * 4]]),
        "two chunks at their offsets, the first of whole base64 quads");
      assert(!pdf.parts[0].endsWith("="), "no padding but the last chunk's");
      assert(savedBytes(pdf).equals(bytes), "the stored PDF, byte for byte");
      assertEq(JSON.stringify((await sent("save")).at(-1)), JSON.stringify({ type: "save", name: "Android saves.pdf", mime: "application/pdf", size: bytes.length }));
      assertEq(JSON.stringify((await sent("saveDone")).at(-1)), JSON.stringify({ type: "saveDone", id: "save-1" }));
      await page.getByRole("status").filter({ hasText: "Exported Android saves.pdf" }).first().waitFor();

      // the lasso's Download SVG: a stroke the host hands back, selected
      const opened = await paperOn("saves");
      contexts.push(opened.ctx);
      const { shell } = opened;
      opened.page.on("download", (d) => downloads.push(d.suggestedFilename()));
      await shell.down(91, 100, 300);
      await shell.stroke(91, Array.from({ length: 8 }, (_, i) => [100 + 15 * i, 300 + (i % 2) * 10, 0.5, 10 * i]));
      await opened.page.keyboard.press("ControlOrMeta+a");
      const menu = opened.page.getByRole("toolbar", { name: "Edit handwriting", exact: true });
      await menu.waitFor();
      const more = menu.getByRole("button", { name: "More", exact: true });
      if (await more.getAttribute("aria-expanded") !== "true") await more.click();
      await menu.getByRole("button", { name: "Convert", exact: true }).click();
      await opened.page.locator(".inkConvertMenu").getByRole("menuitem", { name: "Download SVG", exact: true }).click();
      const svg = await until(async () => (await opened.page.evaluate(() => window.e2eSaved)).find((s) => s.name === "handwriting.svg"),
        { what: "the SVG handed to the app" });
      assertEq(svg.mime, "image/svg+xml;charset=utf-8");
      assert(savedBytes(svg).toString().startsWith("<svg"), "an SVG document");
      await sleep(300);
      assertEq(JSON.stringify(downloads), "[]", "no browser download");
      assertNoProblems(page);
      assertNoProblems(opened.page);

      // an app that refuses the file: the page says so and sends no data
      const refusing = await androidPage(`${server.base}/?ws=${alice.ws}&block=${paper.id}`, { refuseSave: true });
      contexts.push(refusing.ctx);
      await waitForPdf(refusing.page, 1);
      await refusing.page.locator('[data-guide="page.document"]').click();
      await refusing.page.getByRole("menuitem", { name: "Download the PDF", exact: true }).click();
      await refusing.page.getByRole("status").filter({ hasText: "The app did not take Android saves.pdf." }).first().waitFor({ timeout: 15000 });
      assertEq((await refusing.sent("save")).length, 1, "asked once");
      assertEq((await refusing.sent("saveDone")).length, 0, "nothing more after the refusal");
      assertEq(JSON.stringify(await refusing.page.evaluate(() => window.e2eSaved)), "[]");
      assertNoProblems(refusing.page);
    } finally { for (const ctx of contexts) await ctx.close(); }
  });

  await step("android shell: URLs go to the app by themselves: an export's file when it is ready, a file chip's from its menu and its link; the page stays and the browser downloads nothing", async () => {
    const created = await alice.api("/api/pages", { method: "POST", body: { title: "Android chip page" } });
    const file = await alice.upload("/api/upload-file", Buffer.from("x,y\n1,2\n"), "data.csv", "text/csv");
    await alice.api("/api/blocks", { method: "POST", body: { parent_id: created.id, content: `The data: [data.csv](${file.url})` } });
    const { ctx, page, sent } = await androidPage(`${server.base}/?page=${created.id}&ws=${alice.ws}`);
    try {
      const downloads = [];
      page.on("download", (d) => downloads.push(d.suggestedFilename()));
      const saved = () => page.evaluate(() => window.e2eSaved);
      await page.locator(".blockRow .fileChip").first().waitFor({ timeout: 15000 });
      const url = page.url();
      // the chip's menu, then its own download link
      await page.locator(".fileChip", { hasText: "data.csv" }).click({ button: "right" });
      await page.locator(".ctxMenuItem", { hasText: "Download" }).click();
      const [fromMenu] = await until(async () => ((await saved()).length ? saved() : null), { what: "the chip's file handed to the app" });
      assertEq(fromMenu.name, "data.csv");
      assert(fromMenu.url.startsWith(`${server.base}${file.url}`) && fromMenu.url.includes(`ws=${alice.ws}`),
        `the absolute address, with the workspace: ${fromMenu.url}`);
      await page.locator(".fileChip .fileChipDownload").first().evaluate((a) => a.click());
      await until(async () => (await saved()).length === 2, { what: "the link's file handed to the app" });
      assertEq(JSON.stringify((await saved())[1]), JSON.stringify(fromMenu), "the same file, the same way");
      assertEq((await sent("save")).length, 0, "a URL goes by itself, not as data");
      const fetched = await fetch(fromMenu.url, { headers: alice.headers() });
      assertEq(await fetched.text(), "x,y\n1,2\n", "the address serves the file with the session");

      // Export… as an Obsidian vault: the job's file, once ready, by URL
      await page.click("button[aria-label='View']");
      await page.locator(".menuPopover .ctxMenuItem", { hasText: "Export…" }).click();
      const dialog = page.getByRole("dialog", { name: "Export", exact: true });
      await dialog.waitFor();
      await dialog.getByRole("button", { name: "Obsidian", exact: true }).click();
      await dialog.getByRole("button", { name: "Next", exact: true }).click();
      await dialog.getByRole("button", { name: "Export", exact: true }).click();
      await dialog.getByRole("heading", { name: "Export ready", exact: true }).waitFor({ timeout: 15000 });
      const job = await until(async () => (await saved()).find((s) => s.url?.includes("/api/jobs/")), { what: "the export handed to the app" });
      assert(/^http:\/\/[^/]+\/api\/jobs\/[^/]+\/download$/.test(job.url) && job.url.startsWith(server.base), `the job's download: ${job.url}`);
      assert(/-obsidian\.zip$/.test(job.name), `named as the job's file: ${job.name}`);
      const zip = Buffer.from(await (await fetch(job.url, { headers: alice.headers() })).arrayBuffer());
      assertEq(zip.subarray(0, 2).toString(), "PK", "the address serves the zip with the session");
      await dialog.getByRole("button", { name: "Done", exact: true }).click();
      assertEq(page.url(), url, "the page stays");
      await sleep(300);
      assertEq(JSON.stringify(downloads), "[]", "no browser download");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("android shell: a workspace backup's Download goes to the app under the name a browser saves it as", async () => {
    const snap = await alice.api(`/api/workspaces/${alice.ws}/backups`, { method: "POST", body: { label: "e2e-android", uploads: false } });
    const listed = (await alice.api(`/api/workspaces/${alice.ws}/backups`)).backups.find((b) => b.name === snap.name);
    const { ctx, page } = await androidPage(`${server.base}/?ws=${alice.ws}`);
    try {
      await page.waitForSelector(".folderNewBtn");
      await page.locator('[data-guide="header.account"]').click();
      await page.getByRole("menuitem", { name: "Settings…", exact: true }).click();
      await page.getByRole("navigation", { name: "Settings categories" }).getByRole("button", { name: "Backups", exact: true }).click();
      const row = page.locator(".aiProvRow", { hasText: "e2e-android" });
      await row.getByRole("button", { name: "Download", exact: true }).click();
      const [save] = await until(async () => {
        const all = await page.evaluate(() => window.e2eSaved);
        return all.length ? all : null;
      }, { what: "the backup handed to the app" });
      assert(save.url.endsWith(`/api/workspaces/${alice.ws}/backups/${snap.name}/download`), `the backup's download: ${save.url}`);
      assertEq(save.name, listed.filename, "named as the listing names it");
      const served = await fetch(save.url, { headers: alice.headers() });
      assert((served.headers.get("content-disposition") || "").includes(`filename="${listed.filename}"`),
        `the name the server's Content-Disposition gives: ${served.headers.get("content-disposition")}`);
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await alice.api(`/api/workspaces/${alice.ws}/backups/${snap.name}`, { method: "DELETE" });
    }
  });

  await step("android shell: a PDF the app was given becomes its page, read over the port in chunks and let go", async () => {
    const { ctx, page, send } = await androidPage(`${server.base}/?ws=${alice.ws}`);
    try {
      await page.waitForSelector(".folderNewBtn");
      const b64 = Buffer.from(makePdf([["A paper the Android app was handed."]])).toString("base64");
      await page.evaluate((file) => window.e2eHold(file), { id: "A1", name: "Handed to Android.pdf", type: "application/pdf", b64 });
      assertEq(JSON.stringify(await send({ type: "filesOpened" })), "{}", "the page answers at once, with nothing");
      await until(async () => (await page.evaluate(() => window.e2eHostSent)).some((m) => m.type === "fileDone" && m.id === "A1"),
        { what: "the shell lets it go" });
      await waitForPdf(page, 1);
      const pages = (await alice.api("/api/blocks/root/children")).children;
      assert(pages.some((p) => p.content === "Handed to Android.pdf"), "a page titled with its name");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("android shell: View › Server bar hides the app's bar over the port and follows the app's word", async () => {
    const { ctx, page, sent, send } = await androidPage(`${server.base}/?ws=${alice.ws}`);
    try {
      await page.waitForSelector(".folderNewBtn");
      await until(async () => (await sent("shellBar")).length > 0, { what: "the page asks how the bar stands" });
      const view = page.getByRole("button", { name: "View", exact: true });
      const row = page.locator(".menuPopover").getByRole("menuitemcheckbox", { name: "Server bar", exact: true });
      await view.click();
      assertEq(await row.getAttribute("aria-checked"), "true", "the app's bar is up");
      await row.click();
      await until(async () => JSON.stringify((await sent("shellBar")).at(-1)) === JSON.stringify({ type: "shellBar", shown: false }),
        { what: "the row hides it" });
      await until(async () => (await row.getAttribute("aria-checked")) === "false", { what: "the row unchecked" });
      await send({ type: "shellBar", shown: true });
      await until(async () => (await row.getAttribute("aria-checked")) === "true", { what: "the app's word checks it" });
      await view.click();
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("android shell: no folder picker: Zotero's import asks for its .zip, and the Notability picker takes any file", async () => {
    const { ctx, page } = await androidPage(`${server.base}/?ws=${alice.ws}`);
    try {
      await page.waitForSelector(".folderNewBtn");
      const openImport = async () => {
        await page.getByRole("button", { name: "View", exact: true }).click();
        await page.getByRole("menuitem", { name: "Import…", exact: true }).click();
        const dialog = page.getByRole("dialog", { name: "Import", exact: true });
        await dialog.waitFor();
        return dialog;
      };
      let dialog = await openImport();
      await dialog.getByText("Zotero library", { exact: true }).click();
      await dialog.getByRole("button", { name: "Next", exact: true }).click();
      await dialog.getByRole("button", { name: "Choose .zip…", exact: true }).waitFor();
      assertEq(await dialog.getByRole("button", { name: "Choose folder…", exact: true }).count(), 0, "no folder to choose");
      assertEq(await dialog.getByRole("button", { name: "or choose a .zip", exact: true }).count(), 0);
      await page.keyboard.press("Escape");
      await dialog.waitFor({ state: "detached" });
      dialog = await openImport();
      await dialog.getByText("Notability & GoodNotes", { exact: true }).click();
      const chooser = page.waitForEvent("filechooser");
      const next = dialog.getByRole("button", { name: "Next", exact: true });
      if (await next.count()) await next.click();
      await dialog.getByRole("button", { name: "Choose files…", exact: true }).click();
      const input = await (await chooser).element();
      const picked = await input.evaluate((el) => ({ accept: el.accept, multiple: el.multiple, directory: el.webkitdirectory }));
      assertEq(JSON.stringify(picked), JSON.stringify({ accept: "", multiple: true, directory: false }),
        "any file: a .note or .ntb has no type the picker knows");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("android: Settings › Server says the server runs inside the app and updates with it, and offers no check and nothing to pull", async () => {
    await server.manage("create-user", "an-admin", "an-admin-pw");
    await server.manage("set-admin", "an-admin", "on");
    const admin = await new Account(server, "an-admin", "an-admin-pw").login();
    const ctx = await admin.context(browser, { hasTouch: true, viewport: LANDSCAPE, userAgent: ANDROID_UA });
    await androidShell(ctx);
    // the server the app runs (gamma_mobile.py: GAMMA_UPDATES=app, GAMMA_UPDATE_CHECK=off)
    await ctx.route("**/api/admin/server-info*", async (route) => {
      const response = await route.fetch();
      const info = await response.json();
      await route.fulfill({ response, json: { ...info, updates: "app", commit: "", frozen: false, update_available: null, update: null,
        latest: null, latest_error: null } });
    });
    try {
      const page = await openPage(ctx, `${server.base}/?ws=${admin.ws}`);
      await page.waitForSelector(".folderNewBtn");
      await page.getByRole("button", { name: "Account & settings", exact: true }).click();
      await page.getByRole("menuitem", { name: "Settings…", exact: true }).click();
      await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor();
      await page.getByRole("navigation", { name: "Settings categories" }).getByRole("button", { name: "Server", exact: true }).click();
      const updates = page.locator('.settingsPane [data-setting="Updates"]').first();
      await updates.getByText("part of the Gamma app on this device, which brings its updates").waitFor();
      await page.locator(".settingsPane .setStats").getByText("inside the Gamma app", { exact: true }).waitFor();
      assertEq(await updates.getByRole("button", { name: "Check now", exact: true }).count(), 0, "nothing to check");
      assertEq(await page.locator(".settingsPane").getByText(/does not update itself|pull /).count(), 0, "nothing to pull");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("android: Account & sync offers no Open account (its pages lead to a plan's checkout) and links the page on deleting a Gamma Cloud account, linked or not, found by the search too; Help links the privacy policy", async () => {
    // What /api/auth/cloud/status answers: a linked account, then none
    // with cloud sign-in on, then cloud sign-in off.
    let status = { identity: { username: "alice-cloud", email: "alice@example.org", plan: "free", linked_at: "2026-11-01T09:00:00Z" },
      enabled: true, issuer: "https://account.example.org", connected: true };
    const { ctx, page } = await androidPage(`${server.base}/?ws=${alice.ws}`);
    await ctx.route("**/api/auth/cloud/status", (route) => route.fulfill({ json: status }));
    const row = (name) => page.locator(`.settingsPane [data-setting="${name}"]`).first();
    const nav = (name) => page.getByRole("navigation", { name: "Settings categories" }).getByRole("button", { name, exact: true });
    const openAccount = async () => {
      await page.reload();
      await page.waitForSelector(".folderNewBtn");
      await page.getByRole("button", { name: "Account & settings", exact: true }).click();
      await page.getByRole("menuitem", { name: "Settings…", exact: true }).click();
      await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor();
      await page.getByRole("button", { name: "Account & sync", exact: true }).click();
      await row("Gamma Cloud").waitFor();
    };
    const deleteLink = async (what) => {
      const link = row("Delete Gamma Cloud account").getByRole("link", { name: "Delete account", exact: true });
      assertEq(await link.getAttribute("href"), "https://gammapdf.com/delete-account/", `${what}: the page on deleting the account`);
      assertEq(await link.getAttribute("target"), "_blank", `${what}: opened outside the app`);
    };
    try {
      await openAccount();
      const cloud = row("Gamma Cloud");
      await cloud.getByText("alice-cloud", { exact: false }).waitFor();
      assertEq(await cloud.getByRole("link", { name: "Open account" }).count(), 0, "no Open account in the app");
      assert(await cloud.getByRole("button", { name: "Unlink", exact: true }).count(), "Unlink stays");
      await deleteLink("linked");
      // the settings search finds it in the app
      await page.getByRole("searchbox", { name: "Search settings" }).fill("delete cloud");
      await page.locator(".settingsSearchResult").filter({ has: page.getByText("Delete Gamma Cloud account", { exact: true }) }).click();
      await row("Delete Gamma Cloud account").waitFor({ state: "visible" });
      // the privacy policy, under Help
      await nav("Help & diagnostics").click();
      const privacy = row("Privacy policy").getByRole("link", { name: "Open", exact: true });
      assertEq(await privacy.getAttribute("href"), "https://gammapdf.com/privacy/", "Help links the privacy policy");
      assertNoProblems(page);

      // not linked here: the person may have made the account through the app's sign-in all the same
      status = { identity: null, enabled: true, issuer: "https://account.example.org", connected: true };
      await openAccount();
      await row("Gamma Cloud").getByRole("button", { name: "Link Gamma Cloud account", exact: true }).waitFor();
      await deleteLink("not linked");
      status = { identity: null, enabled: false, issuer: "", connected: null };
      await openAccount();
      await deleteLink("cloud sign-in off");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // --- the chrome pages (desktop/ui), as the app serves them --------------
  // The app serves desktop/ui under https://appassets.androidplatform.net/
  // assets/chrome/ (WebViewAssetLoader) and injects its chrome.js there,
  // beside the port `gammaShellHost`, played here: every call recorded in
  // `e2eCalls` and answered on a later task (state and list with `state`).
  // The iPad's and the desktop's shells are plain objects, as their
  // preload.js and Chrome.swift declare them.
  const CHROME = path.join(ROOT, "desktop", "ui");
  const CHROME_JS = fs.readFileSync(path.join(ROOT, "android/app/src/main/assets/shell/chrome.js"), "utf8");
  const TYPES = { ".html": "text/html", ".css": "text/css", ".woff2": "font/woff2", ".woff": "font/woff", ".svg": "image/svg+xml" };
  function chromePort(state) {
    const listeners = new Set();
    window.e2eCalls = [];
    window.gammaShellHost = {
      postMessage(data) {
        const m = JSON.parse(data);
        if (m.hello || m.id == null) return;
        window.e2eCalls.push(m.method);
        const value = m.method === "state" || m.method === "list" ? state : null;
        setTimeout(() => {
          const event = new MessageEvent("message", { data: JSON.stringify({ reply: m.id, value }) });
          for (const fn of listeners) fn(event);
        });
      },
      addEventListener(type, fn) { if (type === "message") listeners.add(fn); },
    };
  }
  function plainShell([platform, state]) {
    window.e2eCalls = [];
    const call = (method) => async () => { window.e2eCalls.push(method); return method === "state" || method === "list" ? state : null; };
    window.gammaShell = { platform, onState: () => () => {} };
    for (const m of ["state", "list", "open", "launcher", "reload", "barExpand", "hideBar", "rename", "remove"]) window.gammaShell[m] = call(m);
  }
  const chromeState = (version) => ({
    servers: [
      { id: "local", type: "local", name: "This device", running: true, url: "http://127.0.0.1:8765", sizeBytes: 2 << 20,
        dataDir: "App storage › Gamma › local", adminUser: "admin", adminPassword: "pw" },
      { id: "lab", type: "remote", name: "Lab", url: "https://lab.example.org", reachable: true },
    ],
    current: { id: "local", type: "local", name: "This device" }, gamma: null, busy: null, theme: "", barHideable: true,
    lastOpened: "local", version, packaged: false,
  });
  const chromePage = async (page, shell) => {
    const ctx = await browser.newContext({ hasTouch: shell !== "win32", viewport: { width: 1000, height: 700 } });
    await ctx.route("https://appassets.androidplatform.net/assets/chrome/**", (route) => {
      const name = decodeURIComponent(new URL(route.request().url()).pathname.replace("/assets/chrome/", ""));
      const file = path.join(CHROME, name);
      if (!file.startsWith(CHROME) || !fs.existsSync(file)) return route.fulfill({ status: 404, body: "" });
      return route.fulfill({ contentType: TYPES[path.extname(file)] || "application/octet-stream", body: fs.readFileSync(file) });
    });
    const state = chromeState(shell === "android" ? "0.1.0" : shell === "ipad" ? "0.2.0" : "1.0.0");
    if (shell === "android") {
      await ctx.addInitScript(chromePort, state);
      await ctx.addInitScript({ content: CHROME_JS });
    } else {
      await ctx.addInitScript(plainShell, [shell, state]);
    }
    const p = await openPage(ctx, `https://appassets.androidplatform.net/assets/chrome/${page}`);
    return { ctx, page: p };
  };
  const look = (p) => p.evaluate(() => {
    const css = (sel) => { const el = document.querySelector(sel); return el && getComputedStyle(el); };
    return {
      platform: document.body.dataset.platform, touch: document.body.hasAttribute("data-touch"),
      device: document.getElementById("thisDevice")?.textContent, version: document.getElementById("version")?.textContent,
      addLocal: css("#btnAddLocal")?.display,
      removes: [...document.querySelectorAll(".card")].map((c) => [c.dataset.id, getComputedStyle(c.querySelector('button[title="Remove server"]')).display !== "none"]),
      ctlBtn: css(".card .ctlBtn")?.width,
    };
  });
  const barLook = (p) => p.evaluate(() => {
    const bar = getComputedStyle(document.getElementById("bar"));
    return { touch: document.body.hasAttribute("data-touch"), height: bar.height, right: bar.paddingRight, top: bar.paddingTop,
      home: document.getElementById("btnHome").title, hide: document.getElementById("btnHideBar").title,
      hideShown: !document.getElementById("btnHideBar").hidden };
  });

  await step("android chrome: the launcher and the bar under the app's chrome.js are a touch screen's, name this device and Gamma for Android, and hide what only the desktop does; the iPad's keep their words, the desktop's nothing of either", async () => {
    const contexts = [];
    try {
      // the launcher
      const launchers = {};
      for (const shell of ["android", "ipad", "win32"]) {
        const { ctx, page } = await chromePage("launcher.html", shell);
        contexts.push(ctx);
        await page.locator('.card[data-id="lab"]').waitFor();
        launchers[shell] = await look(page);
        assertNoProblems(page);
      }
      // the local server's Remove is the desktop's alone; a remote's is everyone's
      assertEq(JSON.stringify(launchers.android), JSON.stringify({ platform: "android", touch: true, device: "this device", version: "Gamma for Android 0.1.0",
        addLocal: "none", removes: [["local", false], ["lab", true]], ctlBtn: "36px" }), "the Android app's launcher");
      assertEq(JSON.stringify(launchers.ipad), JSON.stringify({ platform: "ipad", touch: true, device: "this iPad", version: "Gamma for iPad 0.2.0",
        addLocal: "none", removes: [["local", false], ["lab", true]], ctlBtn: "36px" }), "the iPad's, as before");
      const desktop = launchers.win32;
      assert(!desktop.touch && desktop.device === "this computer" && desktop.version === "Gamma desktop 1.0.0 (dev)" && desktop.addLocal !== "none"
        && desktop.removes.every(([, shown]) => shown) && desktop.ctlBtn !== "36px", `the desktop's, as before: ${JSON.stringify(desktop)}`);

      // the bar
      const bars = {};
      for (const shell of ["android", "ipad", "win32"]) {
        const { ctx, page } = await chromePage("bar.html", shell);
        contexts.push(ctx);
        await until(async () => (await page.locator("#wsName").textContent()) === "This device", { what: `${shell}: the bar shows the server` });
        bars[shell] = { page, look: await barLook(page) };
      }
      assertEq(JSON.stringify(bars.android.look), JSON.stringify({ touch: true, height: "44px", right: "8px", top: "0px",
        home: "All servers (Ctrl+Shift+L)", hide: "Hide this bar (Ctrl+Shift+B)", hideShown: true }), "the Android app's bar");
      assertEq(JSON.stringify(bars.ipad.look), JSON.stringify({ touch: true, height: "44px", right: "8px", top: "0px",
        home: "All servers (⌘⇧L)", hide: "Hide this bar (⌘⇧B)", hideShown: true }), "the iPad's (no inset in this browser)");
      const bare = bars.win32.look;
      assert(!bare.touch && bare.right === "146px" && bare.home === "All servers (Ctrl+Shift+L)", `the desktop's, as before: ${JSON.stringify(bare)}`);
      // the hide chevron calls the app over its port
      await bars.android.page.locator("#btnHideBar").click();
      await until(async () => (await bars.android.page.evaluate(() => window.e2eCalls)).includes("hideBar"), { what: "hideBar reaches the app" });
      for (const { page } of Object.values(bars)) assertNoProblems(page);
    } finally { for (const ctx of contexts) await ctx.close(); }
  });
}
