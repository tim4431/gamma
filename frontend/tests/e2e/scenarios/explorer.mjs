import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Account, Server, makePdf, sleep, wanted } from "../harness.mjs";

// The explorer (docs/research/file-explorer.md step 5; docs/dev/work_folders.md
// "The Files window", "Files by name", "Paths"): a work folder of a paper,
// with its sources, figures, a table and a note. Quick open finds a file by
// name and by path among 20,000 seeded ones; the Files window beside an open
// page reads nothing until it is shown, then reveals the page, opens and
// moves by the keys, folds, closes and comes back as it was; a library
// folder stands in it with its pages; a file's crumbs open its directory;
// a note's link to a file is a mention of its page, and a path typed in
// Gamma opens the file it names from the note's directory, a Gamma-made
// note's too; on a phone the window is an overlay over the page. The
// vaults recipe (GAMMA_FOLDER_LINKS, no shell secret, a desktop user agent).
const DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Gamma/1.0.0 "
  + "Chrome/138.0.0.0 Electron/38.0.0 Safari/537.36";
// What the work folder tracks: its sources, PDFs, notes and the table.
const IGNORE = "*\n!*/\n!*.tex\n!*.pdf\n!*.md\n!*.csv\n";
const NOTES = "# notes\n\nSee [the method](sections/method.tex) and [[main.pdf]].\n";
const PLACEHOLDER = "Search pages, files, folders and labels";
// What a path link naming no file asks: the server's "no tracked file" answer.
const NO_FILE = /GET \/api\/folders\/[^/]+\/at\?\S* -> 404/;

export async function explorerScenarios(env) {
  const { browser, step, openPage, assert, assertEq, assertNoProblems, until } = env;
  if (!wanted("explorer")) return;
  const server = new Server({ env: { GAMMA_FOLDER_LINKS: "1", GAMMA_SHELL_SECRET: "" } });
  const disk = fs.mkdtempSync(path.join(os.tmpdir(), "gamma-e2e-explorer-"));
  try {
    await server.start();
    await server.manage("create-user", "explorer-user", "explorer-pw");
    const user = await new Account(server, "explorer-user", "explorer-pw").login();
    const home = `${server.base}/?ws=${user.ws}`;
    const at = (id) => `${server.base}/?page=${id}&ws=${user.ws}`;
    const desktop = () => user.context(browser, { userAgent: DESKTOP_UA });
    // written as an editor that finished a while ago, so a round takes it at once
    const write = (file, bytes) => {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, bytes);
      const before = new Date(Date.now() - 10_000);
      fs.utimesSync(file, before, before);
    };
    const pagesOf = async (folder) => (await user.api(`/api/folders/${folder}/pages?limit=500`)).pages;
    const fileNamed = async (folder, name) => (await pagesOf(folder)).find((p) => p.properties?.file_name === name);
    // every folder of the listing's tree, at any depth
    const allFolders = async () => (await user.api("/api/blocks/root/children")).folders.children
      .flatMap(function all(f) { return [f, ...(f.children || []).flatMap(all)]; });
    const texts = async (id) => ((await user.api(`/api/blocks/${id}/subtree`)).block.children || [])
      .flatMap(function all(b) { return [b.content, ...(b.children || []).flatMap(all)]; });
    const opened = (page) => { const q = new URL(page.url()).searchParams; return q.get("block") || q.get("page"); };
    const title = async (page) => ((await page.locator(".titleText").first().textContent()) || "").trim();

    // The tree's row of a folder or a file by its name, in the window (or `within`).
    const treeRow = (page, name, within = page) => within.locator('.fileTree [role="treeitem"]')
      .filter({ has: page.locator(`.fileTreeName:text-is("${name}")`) });
    const focusedRow = (page) => page.evaluate(() => {
      const el = document.activeElement;
      return el?.getAttribute("role") === "treeitem" ? el.querySelector(".fileTreeName")?.textContent : null;
    });
    // The View menu's Files row; the menu closed after it if it stays open.
    const openFiles = async (page) => {
      await page.locator('[data-guide="header.view"]').click();
      const menu = page.locator(".menuPopover");
      await menu.getByRole("menuitemcheckbox", { name: "Files", exact: true }).click();
      if (await menu.isVisible()) await page.keyboard.press("Escape");
      await page.locator('[data-guide="files.grip"]').waitFor({ timeout: 15000 });
      await page.locator(".filesPanel .fileTree").waitFor({ timeout: 15000 });
    };
    // A text file's Notes window may start folded beside its code view.
    const unfoldNotes = async (page) => {
      await page.locator(".titleText").first().waitFor({ state: "attached", timeout: 15000 });
      const folded = page.locator(".dockWindow.collapsed", { hasText: "Notes" });
      if (await folded.count()) await folded.click();
    };
    const palette = async (page) => {
      await page.evaluate(() => document.activeElement?.blur?.());
      await page.keyboard.press("ControlOrMeta+p");
      const dialog = page.getByRole("dialog", { name: "Open a page" });
      await dialog.waitFor();
      return { dialog, input: dialog.getByRole("textbox", { name: PLACEHOLDER }) };
    };
    const fileOptions = (dialog) => dialog.locator('[role="option"][data-kind="file"]');
    // A file row by its whole name (a pattern: the query's marks split its
    // text, so :text-is, which reads one text node, would not match).
    const fileOption = (page, dialog, name) => fileOptions(dialog).filter({ has: page.locator("strong", { hasText: name }) });
    // A query typed into quick open and the server's answer to it (the rows
    // of the query before stay while it is asked); that response.
    const search = async (page, input, q) => {
      const answered = page.waitForResponse((r) => {
        const u = new URL(r.url());
        return u.pathname === "/api/files" && u.searchParams.get("q") === q;
      });
      await input.fill(q);
      const response = await answered;
      assertEq(response.status(), 200, `the files read for "${q}" answers`);
      return response;
    };

    // the paper directory: its files first, the note that links them once they are pages
    const paperDir = path.join(disk, "Paper");
    write(path.join(paperDir, "main.tex"), "\\documentclass{article}\n\\begin{document}\\input{sections/intro}\\end{document}\n");
    write(path.join(paperDir, "main.pdf"), Buffer.from(makePdf([["The compiled paper"]])));
    write(path.join(paperDir, "sections", "intro.tex"), "\\section{Introduction}\n");
    write(path.join(paperDir, "sections", "method.tex"), "\\section{Method}\nWe fit a model.\n");
    write(path.join(paperDir, "figures", "fig1.pdf"), Buffer.from(makePdf([["The first figure"]])));
    write(path.join(paperDir, "figures", "fig2.pdf"), Buffer.from(makePdf([["The second figure"]])));
    write(path.join(paperDir, "figures", "raw", "data.csv"), "x,y\n1,2\n");
    write(path.join(paperDir, ".gammaignore"), IGNORE);

    let link = null, W = "";
    const ids = {};
    await step("explorer: a work folder of the paper makes a page of each file, and its note's links are mentions of their pages", async () => {
      link = await user.api("/api/folder-links", { method: "POST", body: { path: paperDir, new_folder: true, pull: true, push: "everything" } });
      W = link.folder_id;
      await user.api(`/api/folder-links/${link.id}/sync?wait=1`, { method: "POST" });
      const folder = (name) => until(async () => (await allFolders()).find((f) => f.content === name)?.id, { timeout: 20000, what: `the ${name} directory's folder` });
      Object.assign(ids, { sections: await folder("sections"), figures: await folder("figures"), raw: await folder("raw") });
      const page = (dir, name) => until(async () => (await fileNamed(dir, name))?.id, { timeout: 30000, what: `${name}'s page` });
      Object.assign(ids, {
        main: await page(W, "main.tex"), mainPdf: await page(W, "main.pdf"),
        intro: await page(ids.sections, "intro.tex"), method: await page(ids.sections, "method.tex"),
        fig1: await page(ids.figures, "fig1.pdf"), fig2: await page(ids.figures, "fig2.pdf"), data: await page(ids.raw, "data.csv"),
      });
      assert(ids.main !== ids.mainPdf, "main.tex and main.pdf are two pages (no main.md pairs them)");
      // a note read once its files are pages: the links become their mentions
      write(path.join(paperDir, "notes.md"), NOTES);
      await user.api(`/api/folder-links/${link.id}/sync?wait=1`, { method: "POST" });
      ids.notes = await page(W, "notes.md");
      const said = await until(async () => (await texts(ids.notes)).find((s) => s.includes(`[[${ids.method}]]`)),
        { timeout: 20000, what: "the note's link to method.tex as its page's mention" });
      assert(said.includes(`[[${ids.mainPdf}]]`), `[[main.pdf]] is main.pdf's mention: ${said}`);
    });

    await step("explorer: quick open finds a file by name and by path among 20,000, under Files, with its directory and the server's marks", async () => {
      const seeded = JSON.parse(await server.python("tests/seed_work_folder.py", user.ws, "Big", "20000"));
      assertEq(seeded.files, 20000, "the seed's files");
      await until(async () => (await allFolders()).some((f) => f.id === seeded.w && f.content === "Big"), { what: "the listing's tree has Big" });
      const target = (await user.api("/api/files?q=file-01234")).files[0];
      assertEq(target?.properties?.file_name, "file-01234.bin", "the server's first row for file-01234");
      const ctx = await desktop();
      const page = await openPage(ctx, home);
      try {
        await page.locator(".fileList").waitFor({ timeout: 15000 });
        let { dialog, input } = await palette(page);
        const response = await search(page, input, "file-01234");
        // (generous: the server's own budget is the perf case's, test_work_folder_scale.py)
        const took = Math.round(response.request().timing().responseStart);
        assert(took < 1500, `the files read answered in ${took} ms`);
        const row = fileOptions(dialog).first();
        await row.waitFor({ timeout: 10000 });
        assertEq(await fileOptions(dialog).count(), 1, "one file has that name");
        const section = await row.evaluate((el) => {
          for (let n = el.previousElementSibling; n; n = n.previousElementSibling) if (n.classList.contains("quickOpenSection")) return n.textContent;
          return "";
        });
        assertEq(section.trim(), "Files", "the row is under Files");
        assertEq((await row.locator("strong").innerText()).trim(), "file-01234.bin", "its name");
        assertEq((await row.locator("strong mark").innerText()).trim(), "file-01234", "the query marked in it");
        assert(/^Big \/ (dir \d+|big)$/.test((await row.locator("small").innerText()).trim()), `its work folder and directory: ${await row.locator("small").innerText()}`);
        for (let n = 0; n < 6 && (await row.getAttribute("aria-selected")) !== "true"; n++) await page.keyboard.press("ArrowDown");
        await page.keyboard.press("Enter");
        await until(() => opened(page) === target.id, { timeout: 15000, what: "the file's page opens" });
        await until(async () => (await title(page)).startsWith("file-01234"), { timeout: 15000, what: "its title" });
        assertNoProblems(page);

        // A slash makes one path term, the directory's space kept: "012" is
        // in names of every directory, only dir 3's are rows.
        ({ dialog, input } = await palette(page));
        await search(page, input, "dir 3/012");
        const inDir = async () => (await fileOptions(dialog).locator("small").allInnerTexts()).map((s) => s.trim());
        await until(async () => { const all = await inDir(); return all.length > 0 && all.every((s) => s === "Big / dir 3"); },
          { timeout: 10000, what: "rows of dir 3 alone (dir 0's file-0012x hold 012 too)" });

        await search(page, input, "method");
        const method = fileOption(page, dialog, /^method\.tex$/);
        await method.waitFor({ timeout: 10000 });
        assertEq((await method.locator("small").innerText()).trim(), "Paper / sections", "the paper's file with its directory");
        await search(page, input, "sections/met");
        await until(async () => (await method.count()) === 1 && (await method.locator("small mark").count()) === 1, { timeout: 10000, what: "method.tex by its path" });
        assertEq((await method.locator("small mark").innerText()).trim(), "sections", "the directory part marked");
        assertEq((await method.locator("strong mark").innerText()).trim(), "met", "and the name's");
        await search(page, input, "sections method");
        await until(async () => (await fileOptions(dialog).count()) === 0, { timeout: 10000, what: "no file row: words match a name alone" });
        await search(page, input, "zzz-none");
        await until(async () => ((await dialog.locator(".quickOpenNote").textContent().catch(() => "")) || "").trim() === "No matching pages or files.",
          { timeout: 10000, what: "the palette's note says nothing matched" });
        await page.keyboard.press("Escape");
        assertNoProblems(page);
        return `file-01234 answered in ${took} ms`;
      } finally { await ctx.close(); }
    });

    await step("explorer: the Files window beside an open page reads nothing until shown, then reveals the page, opens a file, moves by the keys, folds, closes and comes back", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx);
      const asked = [];
      page.on("request", (r) => {
        const m = /^\/api\/folders\/([^/]+)\/pages$/.exec(new URL(r.url()).pathname);
        if (m) asked.push(m[1]);
      });
      try {
        await page.goto(at(ids.main));
        await page.locator(".titleText").first().waitFor({ state: "attached", timeout: 15000 });
        await page.waitForTimeout(1500);
        assertEq(asked.length, 0, "the window closed, nothing of the tree is read");
        assertEq(await page.locator('[data-guide="files.grip"]').count(), 0, "it starts closed");

        await openFiles(page);
        const slot = page.locator('[data-panel-id="slot-left"]');
        await slot.waitFor();
        await until(async () => (await treeRow(page, "main.tex").getAttribute("aria-selected")) === "true", { timeout: 15000, what: "main.tex revealed and selected" });
        assertEq(await treeRow(page, "Paper").getAttribute("aria-expanded"), "true", "its work folder opened");
        assert(asked.includes(W), "the reveal read the work folder's files");
        assert(!asked.includes(ids.figures) && !asked.includes(ids.sections), `no directory still closed is read: ${asked.join(", ")}`);
        const [row, box] = await Promise.all([treeRow(page, "main.tex").boundingBox(), slot.boundingBox()]);
        assert(row.x >= box.x && row.x + row.width <= box.x + box.width + 1, `the row is in the left slot: ${JSON.stringify({ row, box })}`);
        assertEq(await treeRow(page, "sections").getAttribute("aria-expanded"), "false", "sections closed");
        assertEq(await treeRow(page, "intro.tex").count(), 0, "and its files not drawn");

        await treeRow(page, "sections").click();
        await treeRow(page, "method.tex").waitFor({ timeout: 10000 });
        assert(asked.includes(ids.sections), "opening sections read its files");
        await treeRow(page, "method.tex").click();
        await until(() => opened(page) === ids.method, { timeout: 15000, what: "one press opens method.tex" });
        await until(async () => (await title(page)) === "method.tex", { timeout: 15000, what: "its title" });
        await until(async () => (await treeRow(page, "method.tex").getAttribute("aria-selected")) === "true", { what: "the row selected" });

        // the keys: a WAI-ARIA tree
        await treeRow(page, "method.tex").focus();
        await page.keyboard.press("ArrowUp");
        assertEq(await focusedRow(page), "intro.tex", "ArrowUp moves up a row");
        await page.keyboard.press("Enter");
        await until(() => opened(page) === ids.intro, { timeout: 15000, what: "Enter opens intro.tex" });
        // (opening the page may take the focus to it)
        await treeRow(page, "intro.tex").focus();
        await page.keyboard.press("ArrowLeft");
        assertEq(await focusedRow(page), "sections", "ArrowLeft moves to the folder the row is in");
        await page.keyboard.press("ArrowLeft");
        await until(async () => (await treeRow(page, "sections").getAttribute("aria-expanded")) === "false", { what: "ArrowLeft closes it" });
        await page.keyboard.press("ArrowRight");
        await until(async () => (await treeRow(page, "sections").getAttribute("aria-expanded")) === "true", { what: "ArrowRight opens it" });
        await page.keyboard.press("Home");
        assert(await page.evaluate(() => document.activeElement === document.querySelector('.fileTree [role="treeitem"]')), "Home moves to the first row");
        await page.keyboard.press("End");
        assert(await page.evaluate(() => document.activeElement === [...document.querySelectorAll('.fileTree [role="treeitem"]')].at(-1)), "End moves to the last row");
        assertEq(await page.locator('.fileTree [tabindex="0"]').count(), 1, "one row in the tab order");
        assertNoProblems(page);

        // a page opened elsewhere is revealed: its directory opened, nothing else
        await page.goto(at(ids.fig1));
        await until(async () => (await treeRow(page, "fig1.pdf").getAttribute("aria-selected")) === "true", { timeout: 15000, what: "fig1.pdf revealed" });
        assertEq(await treeRow(page, "figures").getAttribute("aria-expanded"), "true", "figures opened");
        assertEq(await treeRow(page, "raw").getAttribute("aria-expanded"), "false", "raw stays closed");

        // fold and open by the grip, close and bring it back from the View menu
        await page.locator('[data-guide="files.grip"]').dblclick();
        await page.locator('.collapsedBar [data-guide="files.grip"]').waitFor({ timeout: 5000 });
        await sleep(500);
        await page.locator('.collapsedBar [data-guide="files.grip"]').dblclick();
        await until(async () => !(await page.locator('.collapsedBar [data-guide="files.grip"]').count())
          && (await page.locator(".filesPanel .fileTree").isVisible()), { what: "the window open again" });
        await page.getByRole("button", { name: "Close Files", exact: true }).click();
        await until(async () => !(await page.locator('[data-guide="files.grip"]').count()), { what: "the window closed" });
        await openFiles(page);
        await until(async () => (await treeRow(page, "figures").getAttribute("aria-expanded")) === "true", { what: "back, open where it was" });
        // the browser keeps it, with its open folders
        await page.reload();
        await page.locator(".filesPanel .fileTree").waitFor({ timeout: 15000 });
        await until(async () => (await treeRow(page, "figures").getAttribute("aria-expanded")) === "true"
          && (await treeRow(page, "sections").getAttribute("aria-expanded")) === "true", { timeout: 15000, what: "its folders open after a reload" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("explorer: a library folder stands in the tree with its page count, opens to its pages, and a press opens one", async () => {
      const shelf = await user.folder("Shelf");
      const filed = await user.api("/api/pages", { method: "POST", body: { title: "Shelf reading", folders: [shelf] } });
      const ctx = await desktop();
      const page = await openPage(ctx, at(ids.main));
      try {
        await openFiles(page);
        const row = treeRow(page, "Shelf");
        await row.waitFor({ timeout: 15000 });
        await until(async () => ((await row.locator(".fileTreeCount").textContent().catch(() => "")) || "").trim() === "1", { what: "Shelf counts its page" });
        await row.click();
        await treeRow(page, "Shelf reading").waitFor({ timeout: 10000 });
        await treeRow(page, "Shelf reading").click();
        await until(() => opened(page) === filed.id, { timeout: 15000, what: "the library page opens" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("explorer: a file's crumbs are its work folder and directories, and one opens that directory, Back returning to the file", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx, at(ids.method));
      try {
        await unfoldNotes(page);
        const crumbs = page.locator(".pageCrumbs .crumbBtn");
        await until(async () => (await crumbs.allInnerTexts()).map((s) => s.trim()).join("|") === "Paper|sections",
          { timeout: 15000, what: "the crumbs Paper / sections" });
        assertEq(await page.locator(".folderChip").count(), 0, "no directory chip beside the title: the crumbs say it");
        await crumbs.filter({ hasText: "sections" }).click();
        await until(async () => ((await page.locator(".folderCurrent .crumbBtn").last().textContent().catch(() => "")) || "").trim() === "sections",
          { timeout: 15000, what: "the directory's list view" });
        for (const name of ["intro.tex", "method.tex"]) {
          await page.locator(".workList .fileRow", { has: page.locator(`.fileRowName:text-is("${name}")`) }).waitFor({ timeout: 15000 });
        }
        const q = new URL(page.url()).searchParams;
        assertEq(q.get("folder"), ids.sections, "the address names the directory");
        assert(!q.get("page") && !q.get("block"), "and no page");
        await page.evaluate(() => document.activeElement?.blur?.());
        await page.keyboard.press("Alt+ArrowLeft");
        await until(() => opened(page) === ids.method, { timeout: 15000, what: "Back returns to method.tex" });

        await page.goto(at(ids.main));
        await unfoldNotes(page);
        await until(async () => (await crumbs.allInnerTexts()).map((s) => s.trim()).join("|") === "Paper",
          { timeout: 15000, what: "a file at the top: its work folder alone" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("explorer: a note's link to a file opens its page; a path typed in Gamma opens the file it names, from a Gamma-made note's directory too", async () => {
      // paused, no round rewrites what is typed here
      await user.api(`/api/folder-links/${link.id}`, { method: "PATCH", body: { paused: true } });
      const ctx = await desktop();
      const page = await openPage(ctx, at(ids.notes));
      try {
        await unfoldNotes(page);
        const chips = page.locator(".blockRow a.blockRefChip");
        await until(async () => { const all = (await chips.allInnerTexts()).map((s) => s.trim()); return all.includes("method.tex") && all.includes("main"); },
          { timeout: 15000, what: "the mentions read method.tex and main (the PDF's stem)" });
        await chips.filter({ hasText: "method.tex" }).click();
        await until(() => opened(page) === ids.method, { timeout: 15000, what: "the mention opens method.tex" });
        assertNoProblems(page);

        await user.api("/api/blocks", { method: "POST", body: { parent_id: ids.notes, content: "Typed: [data](figures/raw/data.csv)" } });
        await user.api("/api/blocks", { method: "POST", body: { parent_id: ids.notes, content: "Gone: [gone](figures/none.csv)" } });
        await page.goto(at(ids.notes));
        await unfoldNotes(page);
        const data = page.locator('.blockRow a.pathLink:text-is("data")');
        await data.waitFor({ timeout: 15000 });
        await data.click();
        await until(() => opened(page) === ids.data, { timeout: 15000, what: "the typed path opens data.csv" });
        await until(async () => (await title(page)) === "data.csv", { timeout: 15000, what: "its title" });
        assertNoProblems(page);

        await page.goto(at(ids.notes));
        await unfoldNotes(page);
        const gone = page.locator('.blockRow a.pathLink:text-is("gone")');
        await gone.waitFor({ timeout: 15000 });
        await gone.click();
        await page.locator(".statusPill", { hasText: "No tracked file at" }).waitFor({ timeout: 10000 });
        assertEq(opened(page), ids.notes, "the note stays open");
        assertNoProblems(page, [NO_FILE]);

        // A note made in sections before any round wrote its file: its base
        // is its directory, not its title's.
        const plan = await user.api("/api/pages", { method: "POST", body: { title: "Plan", folders: [ids.sections] } });
        const props = await until(async () => { const p = (await user.api(`/api/blocks/${plan.id}`)).properties; return p?.work_folder === W && p; },
          { what: "Plan is the work folder's" });
        assert(!props.file_name, "no file written for it yet");
        await user.api("/api/blocks", { method: "POST", body: { parent_id: plan.id, content: "See [m](method.tex)" } });
        await page.goto(at(plan.id));
        await unfoldNotes(page);
        const m = page.locator('.blockRow a.pathLink:text-is("m")');
        await m.waitFor({ timeout: 15000 });
        await m.click();
        await until(() => opened(page) === ids.method, { timeout: 15000, what: "method.tex, from the note's directory" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("explorer: on a phone the Files window is an overlay from the More sheet, a tap on a file opens it, and quick open finds a file", async () => {
      const ctx = await user.context(browser, { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
      const page = await openPage(ctx, at(ids.method));
      try {
        await page.locator(".app.phoneUI .phoneBottomBar").waitFor({ timeout: 15000 });
        const more = page.getByRole("button", { name: "More", exact: true });
        await more.tap();
        await page.locator(".userPopover").getByRole("menuitemcheckbox", { name: "Files", exact: true }).tap();
        const overlay = page.locator(".phonePanel:not(.phonePanelHidden):has(.filesPanel)");
        await overlay.locator(".fileTree").waitFor({ timeout: 15000 });
        await until(async () => (await treeRow(page, "method.tex", overlay).getAttribute("aria-selected")) === "true", { timeout: 15000, what: "method.tex revealed" });
        await treeRow(page, "intro.tex", overlay).tap();
        await until(() => opened(page) === ids.intro, { timeout: 15000, what: "the tap opens intro.tex" });
        await until(async () => !(await overlay.count()), { what: "the overlay gone" });
        // (a phone opens a text file on its Text view, the notes' title not drawn)
        await page.locator(".tabStrip .tab.active", { hasText: "intro.tex" }).waitFor({ timeout: 15000 });
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "nothing scrolls sideways");

        await more.tap();
        await page.locator(".userPopover .ctxMenuItem", { has: page.locator(".ctxMenuText", { hasText: /^Go to page$/ }) }).tap();
        const dialog = page.getByRole("dialog", { name: "Open a page" });
        await dialog.getByRole("textbox", { name: PLACEHOLDER }).fill("fig2");
        const row = fileOption(page, dialog, /^fig2\.pdf$/);
        await row.waitFor({ timeout: 10000 });
        await row.tap();
        await until(() => opened(page) === ids.fig2, { timeout: 15000, what: "the file row opens fig2.pdf" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });
  } finally {
    await server.stop();
    fs.rmSync(disk, { recursive: true, force: true });
  }
}
