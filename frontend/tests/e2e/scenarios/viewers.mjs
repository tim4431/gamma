import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Account, Server, wanted } from "../harness.mjs";
import { png } from "./pictures.mjs";

// Any file as a page (docs/research/file-explorer.md step 3, docs/dev/
// documents.md "Viewers"): a work folder whose files are of every format,
// each a page showing its file in the pane its format gets. The vaults
// recipe (GAMMA_FOLDER_LINKS, no shell secret, a desktop user agent), the
// keep limit lowered to 4 MB so one file is listed without its bytes. The
// pages and their formats; the code view (read-only, coloured by a grammar
// fetched for it alone, CodeMirror's Find under Ctrl+F, Select all and
// Copy, a large file's head and Show all); the picture (the fit, the zoom
// at the pointer, a region drawn and commented on and a thread on the page,
// both in .gamma/feedback/open.json with their pictures within seconds,
// which is the plan's gate, and a rebuilt picture with the region dashed);
// the file pane (Download, Show in folder, the listed file); the players;
// the viewer's words on the tab, the ×, the View menu and a phone's tab; a
// code view read in a second tab group; a share link's visitor; a phone's
// badge and finger-drawn region.
const DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Gamma/1.0.0 "
  + "Chrome/138.0.0.0 Electron/38.0.0 Safari/537.36";
const KEEP_MB = 4;
const PHONE = { hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 } };
const LISTED = "This file is too large to keep in Gamma. It stays in its folder on this computer.";
const JPEG = Buffer.from([0xff, 0xd8, 0xff]);

// A WAV of `seconds` of a sine at `hz`: 8 kHz, mono, 16-bit PCM.
function wav(seconds = 2, hz = 440) {
  const rate = 8000, n = rate * seconds;
  const out = Buffer.alloc(44 + n * 2);
  out.write("RIFF", 0); out.writeUInt32LE(36 + n * 2, 4); out.write("WAVE", 8);
  out.write("fmt ", 12); out.writeUInt32LE(16, 16); out.writeUInt16LE(1, 20); out.writeUInt16LE(1, 22);
  out.writeUInt32LE(rate, 24); out.writeUInt32LE(rate * 2, 28); out.writeUInt16LE(2, 32); out.writeUInt16LE(16, 34);
  out.write("data", 36); out.writeUInt32LE(n * 2, 40);
  for (let i = 0; i < n; i++) out.writeInt16LE(Math.round(Math.sin((2 * Math.PI * hz * i) / rate) * 12000), 44 + i * 2);
  return out;
}

// A LaTeX source of `lines` lines: commands and inline math for the grammar
// to colour (the legacy stex mode: a command is a tag, math a keyword).
const tex = (lines) => ["\\documentclass{article}", "\\usepackage{amsmath}", "\\begin{document}", "\\section{Energy}",
  ...Array.from({ length: lines - 5 }, (_, i) => `The energy $E_{${i}} = \\hbar \\omega_{${i}}$ of level ${i}.`),
  "\\end{document}", ""].join("\n");

// A log past the code view's head (2 MiB): 3 MB of lines.
function log() {
  const parts = [];
  let size = 0;
  for (let i = 0; size < 3 * 1024 * 1024; i++) {
    const line = `2026-11-12 09:00:${String(i % 60).padStart(2, "0")} step ${i} loss ${(1 / (i + 1)).toFixed(6)}\n`;
    parts.push(line);
    size += line.length;
  }
  return parts.join("");
}

export async function viewerScenarios(env) {
  const { browser, step, openPage, assert, assertEq, assertNoProblems, until, sleep } = env;
  if (!wanted("viewers")) return;
  const server = new Server({ env: { GAMMA_FOLDER_LINKS: "1", GAMMA_SHELL_SECRET: "", GAMMA_VAULT_KEEP_MB: String(KEEP_MB) } });
  const disk = fs.mkdtempSync(path.join(os.tmpdir(), "gamma-e2e-viewers-"));
  try {
    await server.start();
    await server.manage("create-user", "view-user", "view-pw");
    const user = await new Account(server, "view-user", "view-pw").login();
    const home = `${server.base}/?ws=${user.ws}`;
    const at = (id) => `${server.base}/?page=${id}&ws=${user.ws}`;
    // A desktop app's window: its user agent, and the messages its page posts to the shell.
    const desktop = async (opts = {}) => {
      const ctx = await user.context(browser, { userAgent: DESKTOP_UA, ...opts });
      await ctx.addInitScript(() => {
        window.shellMessages = [];
        window.addEventListener("message", (e) => {
          if (e.data?.source === "gamma-app" && e.data.type !== "connector-hello") window.shellMessages.push(e.data);
        });
      });
      return ctx;
    };
    const posted = (page) => page.evaluate(() => window.shellMessages);
    // written as an editor that finished a while ago, so a round takes it at once
    const write = (file, bytes) => {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, bytes);
      const before = new Date(Date.now() - 10_000);
      fs.utimesSync(file, before, before);
    };
    const dir = path.join(disk, "Project");
    const jsonText = `${JSON.stringify(Object.fromEntries(Array.from({ length: 60 }, (_, i) => [`run_${i}`, { seed: i, loss: i / 100 }])), null, 2)}\n`;
    const logText = log();
    const logLines = logText.split("\n").length; // as the view counts them: the newlines, and one
    const files = {
      "main.tex": tex(200), "data.json": jsonText, "figures/fig.png": png(1200, 800), "blob.bin": crypto.randomBytes(40_000),
      "tone.wav": wav(), "clip.mp4": wav(1, 660), "run.log": logText, "weights.ckpt": Buffer.alloc(5 * 1024 * 1024),
    };
    for (const [rel, bytes] of Object.entries(files)) write(path.join(dir, rel), bytes);
    const feedbackDir = path.join(dir, ".gamma", "feedback");
    const openJson = () => JSON.parse(fs.readFileSync(path.join(feedbackDir, "open.json"), "utf8"));
    const isJpeg = (file) => fs.existsSync(file) && fs.readFileSync(file).subarray(0, 3).equals(JPEG);

    const pagesOf = async (folder) => (await user.api(`/api/folders/${folder}/pages?limit=500`)).pages;
    const pageNamed = (pages, name) => pages.find((p) => p.properties?.file_name === name);
    // every folder of the listing's tree, at any depth
    const allFolders = async () => (await user.api("/api/blocks/root/children")).folders.children
      .flatMap(function all(f) { return [f, ...(f.children || []).flatMap(all)]; });
    const docOf = async (id) => (await user.api(`/api/blocks/${id}`)).properties?.doc || null;
    const childrenOf = async (id) => (await user.api(`/api/blocks/${id}/subtree`)).block.children || [];
    const threadsOf = async (id) => (await user.api(`/api/pages/${id}/comments?state=all`)).threads;
    const blockOf = (page) => new URL(page.url()).searchParams.get("block");
    // The code view's text box in `scope`, and the line at the top of its view.
    const code = (scope) => scope.locator(".docTextView .cm-content");
    const topLine = (scope) => scope.locator(".docTextView .cm-scroller").evaluate((el) => {
      const top = el.getBoundingClientRect().top;
      const gutter = [...el.querySelectorAll(".cm-lineNumbers .cm-gutterElement")].find((g) => g.getBoundingClientRect().bottom > top + 1);
      return Number(gutter?.textContent || 0);
    });
    // A comment's field is the note editor: its text box is the .cm-content.
    const field = (scope) => scope.locator(".commentField .cm-content");
    const focused = (f, what) => until(async () => f.evaluate((el) => el === document.activeElement).catch(() => false), { what });
    // The picture as drawn (its box) and the zoom it is drawn at.
    const boxOf = (page) => page.locator(".imgViewer .imgBox").boundingBox();
    const scaleOf = (page) => page.locator(".imgViewer .imgLayer").evaluate((el) => Number(el.style.getPropertyValue("--viewer-scale")));
    const pictureLoaded = (img, width) => until(() => img.evaluate((el, w) => el.complete && el.naturalWidth === w, width), { timeout: 20000, what: `the picture loaded (${width} px wide)` });
    // A mouse drag from `from` to `to` (view px): how many marquees were drawn
    // on the way (one, waited for, where a drag draws; none where it may not).
    const dragRegion = async (page, from, to, { draws = true } = {}) => {
      const marquee = page.locator(".imgBox .regionMarquee");
      await page.mouse.move(from.x, from.y);
      await page.mouse.down();
      await page.mouse.move(to.x, to.y, { steps: 10 });
      if (draws) await until(async () => (await marquee.count()) === 1, { what: "the marquee drawn while the mouse drags" });
      else await sleep(150);
      const drawn = await marquee.count();
      await page.mouse.up();
      return drawn;
    };

    let link = null, W = "";
    const ids = {};
    await step("viewers: a work folder's files of every format are pages in their directories' folders, out of the library, the large one listed", async () => {
      link = await user.api("/api/folder-links", { method: "POST", body: { path: dir, new_folder: true, pull: true, push: "everything", choose: true } });
      W = link.folder_id;
      const ctx = await desktop();
      const page = await openPage(ctx, `${home}&folder=${W}&track=${link.id}`);
      try {
        const dialog = page.getByRole("dialog", { name: "Choose files" });
        await dialog.locator('.importTreeFile[title="main.tex"]').waitFor({ timeout: 15000 });
        await dialog.getByRole("button", { name: "Select all", exact: true }).click();
        const summary = dialog.locator(".trackSummary");
        await until(async () => /^8 files,/.test((await summary.innerText()).trim()), { timeout: 10000, what: "the summary counts the eight files" });
        await dialog.getByText(/1 chosen file is over 4\.0 MB and is listed without its bytes/).waitFor({ timeout: 10000 });
        await dialog.getByRole("button", { name: "Save", exact: true }).click();
        await dialog.waitFor({ state: "detached", timeout: 15000 });
        await until(async () => (await pagesOf(W)).length === 7, { timeout: 60000, what: "the round made the top's seven pages" });
        const figures = (await allFolders()).find((f) => f.content === "figures");
        assert(figures, "the figures directory's folder");
        const fig = await until(async () => pageNamed(await pagesOf(figures.id), "fig.png"), { timeout: 20000, what: "the figure's page" });
        const top = await pagesOf(W);
        const want = { "main.tex": ["text", ".tex"], "data.json": ["text", ".json"], "blob.bin": ["binary", ".bin"], "tone.wav": ["audio", ".wav"],
          "clip.mp4": ["video", ".mp4"], "run.log": ["text", ".log"], "weights.ckpt": ["binary", ".ckpt"] };
        for (const [name, [format, ext]] of Object.entries(want)) {
          const p = pageNamed(top, name);
          assert(p, `${name} has a page`);
          assertEq(p.content, name, `${name}'s page is titled by its whole name`);
          assertEq(JSON.stringify([p.properties.doc?.format, p.properties.doc?.ext]), JSON.stringify([format, ext]), `${name}'s document`);
          assertEq(p.properties.doc?.at, name === "weights.ckpt" ? "listed" : "path", `${name}: where its bytes are`);
          assertEq(p.properties.doc?.size, files[name].length, `${name}'s size`);
          assert(!p.properties.library, `${name} is no library document`);
          ids[name] = p.id;
        }
        ids["fig.png"] = fig.id;
        assertEq(JSON.stringify([fig.properties.doc?.format, fig.properties.doc?.at, fig.content]), JSON.stringify(["image", "path", "fig.png"]), "the figure is a picture read where it is");
        const listed = pageNamed(top, "weights.ckpt").properties.doc;
        assertEq(listed.hash, "", "a listed file has no hash");
        const root = new Set((await user.api("/api/blocks/root/children")).children.map((p) => p.id));
        for (const id of Object.values(ids)) assert(!root.has(id), "the listing leaves the work folder's files out");
        // the folder's list view: the Format column says what each file is
        await page.locator(".workList").waitFor({ timeout: 15000 });
        const formatOf = async (name) => (await page.locator(".workList .fileRow", { has: page.locator(`.fileRowName:text-is("${name}")`) })
          .locator(".fileRowFormat").innerText()).trim();
        for (const [name, label] of [["main.tex", "LaTeX"], ["data.json", "JSON"], ["blob.bin", "File"], ["run.log", "Text"],
          ["tone.wav", "Audio"], ["clip.mp4", "Video"], ["weights.ckpt", "File"]]) {
          assertEq(await formatOf(name), label, `${name}'s Format column`);
        }
        await page.goto(`${home}&folder=${figures.id}`);
        await page.locator(".workList .fileRow", { has: page.locator('.fileRowName:text-is("fig.png")') }).waitFor({ timeout: 15000 });
        assertEq(await formatOf("fig.png"), "Picture", "the figure's Format column");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("viewers: main.tex opens in the code view, read-only and coloured once its grammar comes, with no note seeded; the library alone fetches no viewer chunk", async () => {
      const ctx = await desktop();
      const chunks = [];
      ctx.on("request", (r) => { const u = r.url(); if (/\.js(\?|$)/.test(u)) chunks.push(u.split("/").pop()); });
      const page = await openPage(ctx, home);
      try {
        await page.locator(".fileList").waitFor({ timeout: 15000 });
        await sleep(500);
        assert(!chunks.some((c) => /^TextViewer-|stex/i.test(c)), `a cold start of the library fetched no viewer chunk: ${chunks.join(", ")}`);
        await page.goto(at(ids["main.tex"]));
        const content = code(page);
        await content.waitFor({ timeout: 20000 });
        assertEq(await content.getAttribute("contenteditable"), "false", "read-only");
        await until(async () => (await page.locator(".docTextView .cm-lineNumbers .cm-gutterElement").count()) > 10, { what: "line numbers" });
        await until(async () => (await page.locator(".docTextView .cm-content .hljs-name, .docTextView .cm-content .hljs-keyword").count()) > 0,
          { timeout: 20000, what: "the grammar's colours" });
        assert(chunks.some((c) => /^TextViewer-/.test(c)), `the code view's chunk: ${chunks.join(", ")}`);
        assert(chunks.some((c) => /stex/i.test(c)), `the LaTeX grammar's chunk: ${chunks.join(", ")}`);
        assert(!chunks.some((c) => /python/i.test(c)), "nothing for Python");
        assertEq((await page.locator(".docTextLang").innerText()).trim(), "LaTeX", "the bar names the language");
        // typing changes nothing, and no note editor took the focus
        await content.click({ position: { x: 40, y: 10 } });
        await page.keyboard.type("xyz");
        await sleep(200);
        assert(!(await content.innerText()).includes("xyz"), "nothing typed lands in the file");
        assertEq(await page.locator(".blockEditorCm").count(), 0, "no note editor opened");
        assertEq((await childrenOf(ids["main.tex"])).length, 0, "no block seeded");
        await page.locator(".dockWindow.collapsed", { hasText: "Notes" }).waitFor({ timeout: 5000 });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("viewers: Ctrl+F in the code view opens CodeMirror's Find and not Gamma's search; Enter selects the match; the bar's Find does the same", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx, at(ids["main.tex"]));
      try {
        const content = code(page);
        await content.waitFor({ timeout: 20000 });
        await content.click({ position: { x: 40, y: 10 } });
        await page.keyboard.press("Control+f");
        const panel = page.locator(".docTextView .cm-panels-top .cm-search");
        await panel.waitFor();
        assertEq(await page.locator(".searchPopover").count(), 0, "Gamma's search stays shut");
        await page.keyboard.type("section{Energy}");
        await page.keyboard.press("Enter");
        const match = page.locator(".docTextView .cm-searchMatch-selected");
        await match.first().waitFor();
        assertEq((await match.allInnerTexts()).join(""), "section{Energy}", "Enter selects the match");
        await page.keyboard.press("Escape");
        await panel.waitFor({ state: "detached" });
        await page.locator(".docTextBar").getByRole("button", { name: "Find", exact: true }).click();
        await panel.waitFor();
        await page.keyboard.press("Escape");
        await panel.waitFor({ state: "detached" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("viewers: data.json opens as JSON, and Select all then Copy carry the whole file", async () => {
      const ctx = await desktop({ permissions: ["clipboard-read", "clipboard-write"] });
      const page = await openPage(ctx, at(ids["data.json"]));
      try {
        const content = code(page);
        await content.waitFor({ timeout: 20000 });
        assertEq((await page.locator(".docTextLang").innerText()).trim(), "JSON");
        await content.click({ position: { x: 40, y: 10 } });
        await page.keyboard.press("Control+a");
        await page.keyboard.press("Control+c");
        await until(async () => (await page.evaluate(() => navigator.clipboard.readText()).catch(() => "")).replace(/\r\n/g, "\n").trim() === jsonText.trim(),
          { what: "the clipboard holds the whole file" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("viewers: run.log comes as its head from one range request; Show all reads the rest and the line count grows", async () => {
      const ctx = await desktop();
      const statuses = [];
      ctx.on("response", (r) => { if (/\/api\/uploads\/[0-9a-f]+\.log/.test(r.url())) statuses.push(r.status()); });
      const page = await openPage(ctx, at(ids["run.log"]));
      try {
        await code(page).waitFor({ timeout: 30000 });
        const foot = page.locator(".docTextFoot");
        await foot.waitFor();
        assert((await foot.innerText()).includes("Showing the first"), `the foot says what shows: ${await foot.innerText()}`);
        assertEq(statuses[0], 206, `the first request was a range: ${statuses.join(", ")}`);
        const lines = async () => Number(((await page.locator(".docTextMeta").innerText()).match(/^([\d,.\s]+) lines?/) || [])[1]?.replace(/\D/g, "") || 0);
        const head = await lines();
        assert(head > 0 && head < logLines, `the head's lines: ${head} of ${logLines}`);
        await foot.getByRole("button", { name: "Show all", exact: true }).click();
        await foot.waitFor({ state: "detached", timeout: 30000 });
        await until(async () => (await lines()) === logLines, { timeout: 15000, what: `every line (${logLines})` });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("viewers: fig.png opens fitted; Ctrl+wheel zooms around the pointer, the zoom buttons step and Fit to width restores", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx, at(ids["fig.png"]));
      try {
        const img = page.locator(".imgViewer img");
        await img.waitFor({ timeout: 20000 });
        await pictureLoaded(img, 1200);
        const viewer = await page.locator(".imgViewer").evaluate((el) => ({ w: el.clientWidth, h: el.clientHeight }));
        const fit = await boxOf(page);
        assert(fit.width <= viewer.w && fit.height <= viewer.h && fit.width > viewer.w * 0.5, `fitted in the pane: ${JSON.stringify(fit)} in ${JSON.stringify(viewer)}`);
        const fitScale = await scaleOf(page);
        assert(fitScale > 0 && fitScale <= 1, `the fit's scale ${fitScale}`);
        // the picture's pixel under the pointer stays under it (a zoom past
        // the pane's height both ways: a picture shorter than the pane cannot scroll to hold it)
        const at0 = { x: fit.x + fit.width * 0.3, y: fit.y + fit.height * 0.6 };
        const under = async () => { const b = await boxOf(page); return { x: ((at0.x - b.x) / b.width) * 1200, y: ((at0.y - b.y) / b.height) * 800 }; };
        const before = await under();
        await page.mouse.move(at0.x, at0.y);
        await page.keyboard.down("Control");
        await page.mouse.wheel(0, -500);
        await page.keyboard.up("Control");
        await until(async () => (await scaleOf(page)) > fitScale * 1.05, { what: "Ctrl+wheel zoomed in" });
        await sleep(200);
        const after = await under();
        assert(Math.abs(after.x - before.x) < 12 && Math.abs(after.y - before.y) < 12, `the pixel under the pointer stays: ${JSON.stringify(before)} then ${JSON.stringify(after)}`);
        const wheeled = await scaleOf(page);
        await page.getByRole("button", { name: "Zoom in", exact: true }).click();
        await until(async () => /Zoom \d+%/.test(await page.locator(".statusPill").innerText().catch(() => "")), { what: "the zoom's readout" });
        await until(async () => (await scaleOf(page)) > wheeled, { what: "the button stepped in" });
        await page.getByRole("button", { name: "Fit to width", exact: true }).click();
        await until(async () => Math.abs((await scaleOf(page)) - fitScale) < 0.01, { what: "Fit restores the fit" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    let region = null, regionThread = null, pageThread = null, figHash = "";
    await step("viewers: a region drawn on the picture and commented on, and a thread on the page, reach open.json with their pictures within seconds", async () => {
      const ctx = await desktop({ viewport: { width: 1600, height: 900 } });
      const page = await openPage(ctx, at(ids["fig.png"]));
      try {
        const img = page.locator(".imgViewer img");
        await img.waitFor({ timeout: 20000 });
        await pictureLoaded(img, 1200);
        figHash = (await docOf(ids["fig.png"])).hash;
        const box = await boxOf(page);
        const drawn = await dragRegion(page, { x: box.x + box.width * 0.2, y: box.y + box.height * 0.2 }, { x: box.x + box.width * 0.5, y: box.y + box.height * 0.5 });
        assertEq(drawn, 1, "the marquee shows while the mouse drags");
        const tip = page.locator(".plainTip.imgRegionTip");
        await tip.waitFor();
        await tip.getByRole("button", { name: "Comment on this region", exact: true }).click();
        const fresh = page.locator(".imgViewer .commentMargin .commentCard.active .commentNew");
        await fresh.waitFor({ timeout: 10000 });
        await focused(field(fresh), "the new comment's field takes the focus");
        await page.keyboard.type("make the legend larger");
        await page.keyboard.press("Shift+Enter");
        region = await until(async () => (await childrenOf(ids["fig.png"])).find((b) => b.properties?.image_position) || null, { what: "the region block" });
        regionThread = await until(async () => (await threadsOf(ids["fig.png"])).find((th) => th.block_id === region.id), { what: "the region's thread" });
        await page.keyboard.press("Escape");
        const pos = region.properties.image_position;
        assertEq(JSON.stringify([pos.width, pos.height, pos.hash]), JSON.stringify([1200, 800, figHash]), "the region is in the picture's own pixels, on this version");
        assertEq(JSON.stringify(pos.rects), JSON.stringify([pos.boundingRect]), "one rectangle, its frame");
        const r = pos.boundingRect;
        assert([r.x1, r.y1, r.x2, r.y2].every(Number.isInteger), `whole pixels: ${JSON.stringify(r)}`);
        assert(Math.abs(r.x1 - 240) <= 6 && Math.abs(r.y1 - 160) <= 6 && Math.abs(r.x2 - 600) <= 6 && Math.abs(r.y2 - 400) <= 6, `where it was drawn: ${JSON.stringify(r)}`);
        // the card level with the region, and the crop in the notes
        await page.locator(`.commentCard[data-card-for="${region.id}"]`).waitFor();
        const look = await page.evaluate((id) => ({
          card: document.querySelector(`.commentCard[data-card-for="${id}"]`).getBoundingClientRect().top,
          region: document.querySelector(`.imgRegion[data-hl-id="${id}"]`).getBoundingClientRect().top,
        }), region.id);
        assert(Math.abs(look.card - look.region) <= 40, `the card is level with the region: card ${look.card}, region ${look.region}`);
        await until(() => page.locator(".blockAreaSnap:not(.blockAreaSnapPending)").first().evaluate((el) => el.src.startsWith("data:image/")).catch(() => false),
          { timeout: 10000, what: "the notes row shows the crop" });
        // a thread on the page itself: its button is in the Notes window, folded for a file page opened empty
        const folded = page.locator(".dockWindow.collapsed", { hasText: "Notes" });
        if (await folded.count()) await folded.click();
        await page.locator(".pageCommentBtn").click();
        const pop = page.locator(".commentPopover");
        await pop.waitFor();
        await focused(field(pop.locator(".commentNew")), "the page thread's field takes the focus");
        await page.keyboard.type("the colours are too light");
        await page.keyboard.press("Shift+Enter");
        pageThread = await until(async () => (await threadsOf(ids["fig.png"])).find((th) => th.block_id === ids["fig.png"]), { what: "the page's thread" });
        await page.keyboard.press("Escape");
        // the files: written by the hot pass, no sync asked for
        const doc = await until(() => {
          try { const d = openJson(); return d.entries?.length === 2 ? d : null; } catch { return null; }
        }, { timeout: 8000, what: "open.json with the two entries" });
        assert(doc.entries.every((e) => e.file === "figures/fig.png" && e.format === "image"),
          `both under the figure, a picture: ${JSON.stringify(doc.entries.map((e) => [e.file, e.format]))}`);
        const byId = Object.fromEntries(doc.entries.map((e) => [e.id, e]));
        const whole = byId[pageThread.id], part = byId[regionThread.id];
        assertEq(whole.kind, "file", "the page's thread is on the whole file");
        assertEq(part.kind, "region");
        assertEq(JSON.stringify(part.where.rect), JSON.stringify([r.x1, r.y1, r.x2, r.y2]), "the region's rectangle in the picture's pixels");
        assertEq(JSON.stringify(part.where.size), JSON.stringify([1200, 800]), "and the picture's size");
        assertEq(part.where.placed, "exact");
        await until(() => [whole, part].every((e) => e.picture && isJpeg(path.join(feedbackDir, e.picture))), { timeout: 8000, what: "each entry's picture written as a JPEG" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("viewers: a rebuilt picture shows in the open view; the region stays, dashed, and open.json says it was drawn on an earlier version, with a before", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx, at(ids["fig.png"]));
      try {
        const img = page.locator(".imgViewer img");
        await img.waitFor({ timeout: 20000 });
        await pictureLoaded(img, 1200);
        write(path.join(dir, "figures", "fig.png"), png(1000, 700));
        await user.api(`/api/folder-links/${link.id}/sync?wait=1`, { method: "POST" });
        await until(async () => { const d = await docOf(ids["fig.png"]); return d?.hash && d.hash !== figHash; }, { timeout: 30000, what: "the page's document is the new picture" });
        await pictureLoaded(img, 1000);
        const drawn = page.locator(`.imgRegion.earlier[data-hl-id="${region.id}"]`);
        await drawn.waitFor({ timeout: 10000 });
        assertEq(await drawn.getAttribute("title"), "Drawn on an earlier version of this picture");
        const entry = await until(() => {
          try { const e = openJson().entries.find((x) => x.id === regionThread.id); return e?.where?.placed === "earlier" ? e : null; } catch { return null; }
        }, { timeout: 15000, what: "the region's entry says earlier" });
        assert(entry.picture && isJpeg(path.join(feedbackDir, entry.picture)), "its crop of the new picture");
        assert(entry.before && isJpeg(path.join(feedbackDir, entry.before)), "and the crop as it was commented on");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("viewers: blob.bin opens as the file pane, its Download the same bytes and Show in folder a message to the shell; weights.ckpt is listed with neither", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx, at(ids["blob.bin"]));
      try {
        const pane = page.locator(".filePane");
        await pane.waitFor({ timeout: 20000 });
        assertEq((await pane.locator(".filePaneName").innerText()).trim(), "blob.bin");
        const meta = (await pane.locator(".filePaneMeta").innerText()).trim();
        assert(/^File · \d+ KB$/.test(meta), `its format and size: ${meta}`);
        const href = await pane.locator("a[download]").getAttribute("href");
        assert(href, "a Download link");
        const got = await page.evaluate(async (u) => { const r = await fetch(u); return [r.status, Array.from(new Uint8Array(await r.arrayBuffer()))]; }, href);
        assertEq(got[0], 200, "the link serves the file");
        assert(Buffer.from(got[1]).equals(files["blob.bin"]), "Download gives the file's bytes");
        await pane.getByRole("button", { name: "Show in folder", exact: true }).click();
        await until(async () => (await posted(page)).some((m) => m.type === "reveal-on-disk"), { what: "the message went" });
        assertEq(JSON.stringify((await posted(page)).map(({ type, ws, page: p }) => ({ type, ws, page: p }))),
          JSON.stringify([{ type: "reveal-on-disk", ws: user.ws, page: ids["blob.bin"] }]), "Show in folder asks the shell");
        await page.goto(at(ids["weights.ckpt"]));
        await pane.waitFor({ timeout: 20000 });
        await until(async () => (await pane.locator(".filePaneName").innerText()).trim() === "weights.ckpt", { what: "the listed file's pane" });
        assertEq((await pane.locator(".filePaneHint").innerText()).trim(), LISTED);
        assertEq(await pane.locator("a[download]").count(), 0, "no Download for a listed file");
        assert((await pane.locator(".filePaneMeta").innerText()).includes("5.0 MB"), "its size from the listing");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("viewers: tone.wav plays in the audio player, read in ranges; clip.mp4 is a video that pauses when its pane closes", async () => {
      const ctx = await desktop();
      const ranges = [];
      ctx.on("response", (r) => { if (/\/api\/uploads\/[0-9a-f]+\.(wav|mp4)/.test(r.url()) && r.status() === 206) ranges.push(r.url()); });
      const page = await openPage(ctx, at(ids["tone.wav"]));
      try {
        const audio = page.locator(".mediaViewer audio");
        await audio.waitFor({ timeout: 20000 });
        assert((await audio.getAttribute("src")).includes((await docOf(ids["tone.wav"])).hash), "the player's src is the file");
        await audio.evaluate(async (el) => { el.muted = true; await el.play(); el.currentTime = 1; });
        await until(() => audio.evaluate((el) => !el.paused && el.currentTime > 0), { what: "it plays" });
        await until(() => ranges.some((u) => u.includes(".wav")), { what: "a range answered 206" });
        await page.goto(at(ids["clip.mp4"]));
        const video = page.locator(".mediaViewer video");
        await video.waitFor({ timeout: 20000 });
        assert((await video.getAttribute("src")).includes((await docOf(ids["clip.mp4"])).hash), "the video's src is the file");
        assert(await video.evaluate((el) => el.hasAttribute("playsinline")), "playsinline");
        await video.evaluate(async (el) => { el.muted = true; await el.play(); });
        await until(() => video.evaluate((el) => !el.paused), { what: "it plays" });
        await page.getByRole("button", { name: "Close the player", exact: true }).click();
        await until(() => video.evaluate((el) => el.paused), { what: "closing the pane pauses it" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("viewers: the tab's glyph, the × and the View menu's window row say the viewer's word, never PDF; so does a phone's tab", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx, at(ids["main.tex"]));
      try {
        const windowRow = async () => {
          await page.getByRole("button", { name: "View", exact: true }).click();
          const menu = page.locator(".menuPopover");
          await menu.waitFor();
          const items = (await menu.locator(".ctxMenuItem").allInnerTexts()).map((s) => s.trim());
          await page.keyboard.press("Escape");
          await menu.waitFor({ state: "detached" });
          return items;
        };
        for (const [name, format, word, close] of [["main.tex", "text", "Text", "Close the file view"], ["fig.png", "image", "Picture", "Close the picture"],
          ["tone.wav", "audio", "Player", "Close the player"], ["blob.bin", "binary", "File", "Close the file view"]]) {
          await page.goto(at(ids[name]));
          await page.locator(".tabStrip .tab", { hasText: name }).first().locator(`.fileGlyph.${format}`).waitFor({ timeout: 20000 });
          const closeBtn = page.locator(".pdfCloseBtn");
          await closeBtn.waitFor();
          assertEq(await closeBtn.getAttribute("aria-label"), close, `${name}: the × names its pane`);
          const items = await windowRow();
          assert(items.includes(word) && !items.includes("PDF"), `${name}: the View menu's row says ${word}: ${items.join("|")}`);
        }
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const phone = await user.context(browser, PHONE);
      const small = await openPage(phone, at(ids["fig.png"]));
      try {
        await small.waitForSelector(".app.phoneUI .phoneBottomBar");
        const tabs = async () => (await small.locator(".phoneTabBar .phoneTab").allInnerTexts()).map((s) => s.trim());
        await until(async () => (await tabs()).includes("Picture"), { what: "the phone tab says Picture" });
        assert(!(await tabs()).includes("PDF"), `no PDF tab: ${(await tabs()).join("|")}`);
        assertNoProblems(small);
      } finally { await phone.close(); }
    });

    await step("viewers: main.tex in a second tab group reads read-only; a press makes it live at the same line, a drag across its text does not", async () => {
      await user.api("/api/prefs/open-tabs", { method: "PUT", body: { value: [] } });
      const serverTabs = async () => ((await user.api("/api/prefs/open-tabs")).value || []).map((tab) => tab.title);
      const ctx = await desktop();
      const page = await openPage(ctx, at(ids["main.tex"]));
      try {
        await code(page).waitFor({ timeout: 20000 });
        await until(async () => (await serverTabs()).includes("main.tex"), { what: "the tab reached the server" });
        await page.goto(at(ids["data.json"]));
        await until(async () => (await page.locator(".topbar .tabStrip .tab").count()) === 2, { timeout: 15000, what: "both tabs" });
        await page.locator(".topbar .tabStrip .tab", { hasText: "main.tex" }).first().click({ button: "right" });
        await page.getByRole("menuitem", { name: "Split right" }).click();
        const pane = page.locator(".tabGroup.framed:not(.live) .pagePane");
        const read = code(pane);
        await read.waitFor({ timeout: 20000 });
        assertEq(await read.getAttribute("contenteditable"), "false", "read-only in the pane");
        assertEq(blockOf(page), ids["data.json"], "data.json stays live");
        // the pane scrolled to a line of the file
        await pane.locator(".docTextView .cm-scroller").evaluate((el) => { el.scrollTop = el.scrollHeight * 0.5; });
        await sleep(300);
        const line = await topLine(pane);
        assert(line > 20, `the pane scrolled to line ${line}`);
        // a drag across the text (in the view: the content's box is scrolled away) selects, and makes nothing live
        const box = await pane.locator(".docTextView .cm-scroller").boundingBox();
        await page.mouse.move(box.x + 80, box.y + 30);
        await page.mouse.down();
        await page.mouse.move(box.x + 250, box.y + 80, { steps: 8 });
        await page.mouse.up();
        await sleep(400);
        assertEq(blockOf(page), ids["data.json"], "a drag-selection leaves the live page as it was");
        // a press makes it live, at the same line
        await page.mouse.click(box.x + 80, box.y + 120);
        await until(() => blockOf(page) === ids["main.tex"], { what: "main.tex opens in the workbench" });
        const liveView = page.locator(".tabLiveHost");
        await code(liveView).waitFor({ timeout: 20000 });
        await until(async () => Math.abs((await topLine(liveView)) - line) <= 2, { timeout: 10000, what: `the same top line (${line})` });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("viewers: a share link of the picture's page shows the region and its card with no region tool, and a shared .tex reads in the code view through the share", async () => {
      const { token } = await user.api(`/api/share/${ids["fig.png"]}`, { method: "POST" });
      const ctx = await browser.newContext({ viewport: { width: 1600, height: 900 } });
      const page = await openPage(ctx, `${server.base}/?share=${token}`);
      try {
        const img = page.locator(".imgViewer img");
        await img.waitFor({ timeout: 20000 });
        await pictureLoaded(img, 1000);
        assert((await img.getAttribute("src")).includes("share="), "read through the share token");
        await page.locator(`.imgRegion[data-hl-id="${region.id}"]`).waitFor();
        await page.locator(`.commentCard[data-card-for="${region.id}"]`).waitFor({ timeout: 10000 });
        const box = await boxOf(page);
        const drawn = await dragRegion(page, { x: box.x + box.width * 0.6, y: box.y + box.height * 0.6 }, { x: box.x + box.width * 0.85, y: box.y + box.height * 0.85 }, { draws: false });
        await sleep(300);
        assertEq(drawn + (await page.locator(".regionMarquee, .imgRegionTip").count()), 0, "a visitor draws no region");
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const { token: texToken } = await user.api(`/api/share/${ids["main.tex"]}`, { method: "POST" });
      const ctx2 = await browser.newContext({ viewport: { width: 1280, height: 860 } });
      const fetched = [];
      ctx2.on("request", (r) => { if (/\/api\/uploads\/[0-9a-f]+\.tex/.test(r.url())) fetched.push(r.url()); });
      const page2 = await openPage(ctx2, `${server.base}/?share=${texToken}`);
      try {
        await code(page2).waitFor({ timeout: 20000 });
        await until(async () => (await code(page2).innerText()).includes("documentclass"), { what: "the file's text" });
        assert(fetched.length && fetched.every((u) => u.includes("share=")), `the file read through the share: ${fetched.join(", ")}`);
        assertNoProblems(page2);
      } finally { await ctx2.close(); }
    });

    await step("viewers: on a phone the picture has no margin, a badge on the region opens its thread, and the strip's toggle lets a finger draw", async () => {
      const ctx = await user.context(browser, PHONE);
      const page = await openPage(ctx, at(ids["fig.png"]));
      try {
        await page.waitForSelector(".app.phoneUI .phoneBottomBar");
        const img = page.locator(".imgViewer img");
        await img.waitFor({ timeout: 20000 });
        await pictureLoaded(img, 1000);
        assertEq(await page.locator(".imgViewer .commentMargin").count(), 0, "no margin on a phone");
        const badge = page.locator(`.imgBox .pdfNoteBadge[data-hl-id="${region.id}"]`);
        await badge.waitFor();
        await badge.tap();
        const pop = page.locator(".commentPopover");
        await pop.waitFor();
        await pop.getByText("make the legend larger").waitFor();
        await page.keyboard.press("Escape");
        await pop.waitFor({ state: "detached" });
        // a finger alone pans; the strip's toggle arms the region drag
        const cdp = await page.context().newCDPSession(page);
        const finger = async (from, to) => {
          await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [from] });
          for (let i = 1; i <= 8; i++) {
            await cdp.send("Input.dispatchTouchEvent", { type: "touchMove",
              touchPoints: [{ x: Math.round(from.x + ((to.x - from.x) * i) / 8), y: Math.round(from.y + ((to.y - from.y) * i) / 8) }] });
            await sleep(16);
          }
          // held still before the lift: a lift mid-move is a fling, and the browser drops the tap that follows one
          await sleep(150);
          await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
        };
        const corners = async () => {
          const b = await boxOf(page);
          return [{ x: Math.round(b.x + b.width * 0.55), y: Math.round(b.y + b.height * 0.55) }, { x: Math.round(b.x + b.width * 0.9), y: Math.round(b.y + b.height * 0.9) }];
        };
        await finger(...(await corners()));
        await sleep(300);
        assertEq(await page.locator(".imgRegionTip").count(), 0, "a finger alone draws nothing");
        // the finger on the toggle as the screen gets it (a locator's tap after the badge's sends no click)
        const toggle = await page.locator('[data-guide="viewer.selectMode"]').boundingBox();
        await page.touchscreen.tap(toggle.x + toggle.width / 2, toggle.y + toggle.height / 2);
        await page.locator(".imgBox.regionMode").waitFor();
        await finger(...(await corners()));
        await page.locator(".imgRegionTip").waitFor({ timeout: 5000 });
        await page.keyboard.press("Escape");
        await page.locator(".imgRegionTip").waitFor({ state: "detached" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });
  } finally {
    await server.stop();
    fs.rmSync(disk, { recursive: true, force: true });
  }
}
