import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Account, Server, makePdf, wanted } from "../harness.mjs";

// Work folders (docs/dev/work_folders.md): a directory of this computer that
// pushes everything is a work folder whose files are chosen in the tracking
// dialog (Choose files…) and written as its .gammaignore. The same backend as
// the vaults group (GAMMA_FOLDER_LINKS, no shell secret, so the API names
// paths as the shell would) and a browser that says it is the desktop app.
// One paper directory: the dialog's preset, the folder's list view, the
// library gate (a file is no paper until it is added), Add to library,
// Remove and Save a copy, the one-place rule, a change of selection, notes
// that follow Gamma, Cancel of a fresh work folder, and a file in the tabs,
// recents and pins the listing does not carry.
const DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Gamma/1.0.0 "
  + "Chrome/138.0.0.0 Electron/38.0.0 Safari/537.36";
const HEADER = "# What Gamma tracks in this folder";

export async function workFolderScenarios(env) {
  const { browser, step, openPage, assert, assertEq, assertNoProblems, until } = env;
  if (!wanted("work-folders")) return;
  const server = new Server({ env: { GAMMA_FOLDER_LINKS: "1", GAMMA_SHELL_SECRET: "" } });
  const disk = fs.mkdtempSync(path.join(os.tmpdir(), "gamma-e2e-work-"));
  try {
    await server.start();
    await server.manage("create-user", "work-user", "work-pw");
    const user = await new Account(server, "work-user", "work-pw").login();
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
    const listing = async () => (await user.api("/api/blocks/root/children")).children;
    // every folder of the listing's tree, at any depth
    const allFolders = async () => (await user.api("/api/blocks/root/children")).folders.children
      .flatMap(function all(f) { return [f, ...(f.children || []).flatMap(all)]; });
    const filesOf = async (folder) => (await user.api(`/api/folders/${folder}/pages?limit=500`)).pages;
    const fileNamed = async (folder, name) => (await filesOf(folder)).find((p) => p.properties?.file_name === name);
    const sync = (link) => user.api(`/api/folder-links/${link.id}/sync?wait=1`, { method: "POST" });
    const make = (dir) => user.api("/api/folder-links", {
      method: "POST", body: { path: dir, new_folder: true, pull: true, push: "everything", choose: true } });
    const papers = async () => (await user.api("/api/metadata/status")).papers.map((p) => p.id);
    const menuItems = async (page, target) => {
      await target.click({ button: "right" });
      const menu = page.locator(".ctxMenu");
      await menu.locator(".ctxMenuItem").first().waitFor();
      const items = (await menu.locator(".ctxMenuItem").allInnerTexts()).map((s) => s.trim());
      await page.keyboard.press("Escape");
      await menu.waitFor({ state: "detached" });
      return items;
    };
    const pick = async (page, target, item) => {
      await target.click({ button: "right" });
      await page.locator(".ctxMenu .ctxMenuItem", { hasText: item }).click();
    };
    const fileRow = (page, name) => page.locator(".workList .fileRow", { has: page.locator(`.fileRowName:text-is("${name}")`) });
    const folderRow = (page, name) => page.locator(".fileList .folderRow", { has: page.locator(`.folderName:text-is("${name}")`) });
    const treeFile = (dialog, rel) => dialog.locator(`.importTreeFile[title="${rel}"]`);
    const treeBox = (dialog, rel) => treeFile(dialog, rel).locator("input[type=checkbox]");

    // the paper directory
    const paperDir = path.join(disk, "Paper");
    write(path.join(paperDir, "main.tex"), "\\documentclass{article}\n\\begin{document}Hi\\end{document}\n");
    write(path.join(paperDir, "refs.bib"), "@article{a, title={A}}\n");
    write(path.join(paperDir, ".gitignore"), "build/\n*.pdf\n");
    write(path.join(paperDir, "main.pdf"), Buffer.from(makePdf([["The compiled paper"]])));
    write(path.join(paperDir, "figures", "fig1.pdf"), Buffer.from(makePdf([["The first figure"]])));
    write(path.join(paperDir, "figures", "fig2.pdf"), Buffer.from(makePdf([["The second figure"]])));
    write(path.join(paperDir, "figures", "plot.py"), "print('plot')\n");
    write(path.join(paperDir, "notes.md"), "# notes\n\nWhat to write next.\n");
    write(path.join(paperDir, "build", "main.aux"), "\\relax\n");
    const ignoreFile = path.join(paperDir, ".gammaignore");

    let link = null;
    await step("work-folders: a work folder made to choose its files runs no round until they are chosen", async () => {
      link = await make(paperDir);
      assert(link.folder_id, "its folder was made");
      assertEq((await filesOf(link.folder_id)).length, 0, "no page yet");
      assert(fs.existsSync(path.join(paperDir, ".gamma", "vault.json")), "its marker is written");
      assert(!fs.existsSync(ignoreFile), "no .gammaignore before the Save");
      const ran = await user.api(`/api/folder-links/${link.id}/sync`, { method: "POST", raw: true });
      assertEq(ran.status, 409, "a sync is refused while it chooses");
    });

    let ids = {};
    await step("work-folders: the dialog ticks the PDFs and notes outside git-ignored directories, and the Save writes .gammaignore", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx, `${home}&folder=${link.folder_id}&track=${link.id}`);
      try {
        const dialog = page.getByRole("dialog", { name: "Choose files" });
        await treeFile(dialog, "notes.md").waitFor({ timeout: 15000 });
        assert(!new URL(page.url()).searchParams.has("track"), "the hand-over leaves the address");
        for (const rel of ["notes.md", "main.pdf", "figures/fig1.pdf", "figures/fig2.pdf"]) {
          assert(await treeBox(dialog, rel).isChecked(), `${rel} is ticked`);
        }
        for (const rel of ["main.pdf", "figures/fig1.pdf", "figures/fig2.pdf"]) {
          assert((await treeFile(dialog, rel).innerText()).includes("git ignores it"), `${rel} says git ignores it`);
        }
        assert(!(await treeBox(dialog, "main.tex").isChecked()), "a LaTeX source is not in the preset");
        // build/, which git ignores, is a closed row read when it opens
        const build = dialog.locator("details", { has: page.locator("summary span:text-is('build')") });
        assertEq(await build.evaluate((d) => d.open), false, "build/ starts closed");
        assertEq(await treeFile(dialog, "build/main.aux").count(), 0, "and unread");
        await build.locator("summary").click();
        await treeFile(dialog, "build/main.aux").waitFor({ timeout: 10000 });
        assert(!(await treeBox(dialog, "build/main.aux").isChecked()), "its file stays unticked");
        // the PDFs filter unticks every PDF, then ticks them again
        const pdfs = dialog.getByRole("button", { name: "PDFs", exact: true });
        assertEq(await pdfs.getAttribute("aria-pressed"), "true", "every PDF is ticked");
        await pdfs.click();
        await until(async () => !(await treeBox(dialog, "main.pdf").isChecked()), { what: "the PDFs unticked" });
        await pdfs.click();
        await until(() => treeBox(dialog, "figures/fig1.pdf").isChecked(), { what: "the PDFs ticked again" });
        await treeBox(dialog, "figures/fig2.pdf").uncheck();
        const summary = dialog.locator(".trackSummary");
        await until(async () => /^3 files,/.test((await summary.innerText()).trim()), { timeout: 10000, what: "the summary counts 3 files" });
        await dialog.getByRole("button", { name: "Save", exact: true }).click();
        await dialog.waitFor({ state: "detached", timeout: 15000 });

        const text = fs.readFileSync(ignoreFile, "utf8");
        assert(text.startsWith(HEADER), "the file starts with Gamma's header");
        const tree = (await user.api(`/api/folder-links/${link.id}/tree?base=file`)).entries;
        const chosen = tree.filter((e) => !e.directory && e.selected).map((e) => e.path).sort();
        assertEq(JSON.stringify(chosen), JSON.stringify(["figures/fig1.pdf", "main.pdf", "notes.md"]), "the file reads back to the choice");
        await until(async () => (await filesOf(link.folder_id)).length === 2, { timeout: 30000, what: "the round made the top's files" });
        const figures = (await allFolders()).find((f) => f.content === "figures");
        assert(figures, "the directory's folder");
        ids = {
          main: (await fileNamed(link.folder_id, "main.pdf")).id,
          notes: (await fileNamed(link.folder_id, "notes.md")).id,
          fig1: (await until(() => fileNamed(figures.id, "fig1.pdf"), { timeout: 20000, what: "the figure's page" })).id,
          figures: figures.id,
        };

        // the folder's list view: its directory first with what it holds, then the files
        await page.locator(".workList").waitFor({ timeout: 15000 });
        await folderRow(page, "figures").waitFor({ timeout: 15000 });
        assertEq((await folderRow(page, "figures").locator(".fileRowFormat").innerText()).trim(), "1 file", "figures holds one file");
        await fileRow(page, "main.pdf").waitFor();
        await fileRow(page, "notes.md").waitFor();
        const names = (await page.locator(".workList .fileRowName, .workList .folderName").allInnerTexts()).map((s) => s.trim());
        assertEq(names.join("|"), "figures|main.pdf|notes.md", "the directory, then the files by name");
        assertEq((await fileRow(page, "main.pdf").locator(".fileRowFormat").innerText()).trim(), "PDF", "its format");
        assert(/\d/.test(await fileRow(page, "main.pdf").locator(".fileRowSize").innerText()), "its size");
        assertEq((await fileRow(page, "notes.md").locator(".fileRowSize").innerText()).trim(), "—", "a note has no size");
        assertEq((await fileRow(page, "main.pdf").locator(".fileRowComments").innerText()).trim(), "", "no comment");
        assertEq(await page.locator(".homeViewToggle").count(), 0, "neither the view nor the kind toggle");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("work-folders: a file not in the library is no paper: not listed, no record looked up, no Paper metadata", async () => {
      const listed = new Set((await listing()).map((p) => p.id));
      for (const id of [ids.main, ids.notes, ids.fig1]) assert(!listed.has(id), "the listing leaves the file out");
      const ctx = await desktop();
      const page = await openPage(ctx, home);
      try {
        await page.locator(".fileList").waitFor({ timeout: 15000 });
        for (const name of ["main", "main.pdf", "notes", "fig1"]) {
          assertEq(await page.locator(`.fileRow:has(.fileRowName:text-is("${name}"))`).count(), 0, `the library's root shows no ${name}`);
        }
        const fetched = [];
        page.on("request", (r) => { if (new URL(r.url()).pathname === "/api/metadata/fetch") fetched.push(r.url()); });
        await page.goto(at(ids.main));
        await page.locator(".titleText").waitFor({ timeout: 15000 });
        await page.waitForTimeout(1500);
        assertEq(fetched.length, 0, "opening it looks no record up");
        assertEq(await page.locator('[data-popover="meta"]').count(), 0, "no Paper metadata button");
        assert(!(await papers()).some((id) => [ids.main, ids.fig1].includes(id)), "the metadata status lists none");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("work-folders: Add to library, Remove from library and Save a copy to library… from the row menu", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx, `${home}&folder=${link.folder_id}`);
      try {
        const row = fileRow(page, "main.pdf");
        await row.waitFor({ timeout: 15000 });
        await pick(page, row, "Add to library…");
        const picker = page.locator(".folderPicker");
        await picker.waitFor();
        await picker.locator(".folderPickerList .ctxMenuItem", { hasText: "All files" }).click();
        await picker.waitFor({ state: "detached" });
        await until(async () => (await listing()).some((p) => p.id === ids.main), { timeout: 15000, what: "main.pdf in the library's root" });
        await until(async () => (await papers()).includes(ids.main), { what: "the metadata status has it" });

        await pick(page, row, "Remove from library");
        await until(async () => !(await listing()).some((p) => p.id === ids.main), { timeout: 15000, what: "out of the library's root" });
        assertEq((await user.api(`/api/blocks/${ids.main}`)).content, "main", "its title is its file's stem again");

        const before = new Set((await listing()).map((p) => p.id));
        await pick(page, row, "Save a copy to library…");
        await picker.waitFor();
        await picker.locator(".folderPickerList .ctxMenuItem", { hasText: "All files" }).click();
        await picker.waitFor({ state: "detached" });
        const copy = await until(async () => (await listing()).find((p) => !before.has(p.id) && p.properties?.doc?.format === "pdf"),
          { timeout: 15000, what: "a new library page" });
        const file = await user.api(`/api/blocks/${ids.main}`);
        assert(!file.properties.library && file.properties.work_folder === link.folder_id, "the file's page is untouched");
        assert(!copy.properties.work_folder, "the copy is a library page");
        const again = await user.api(`/api/pages/${ids.main}/library-copy`, { method: "POST", body: {} });
        assertEq(again.page.id, copy.id, "a second copy answers the same page");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("work-folders: a file and the folders in a work folder stay where the disk has them", async () => {
      const shelf = await user.folder("Shelf");
      const refused = await user.api(`/api/blocks/${ids.fig1}`, { method: "PUT", raw: true,
        body: { properties: { folders: [shelf] } } });
      assertEq(refused.status, 403, "refiling a file is refused");
      assert((await refused.text()).includes("locked"), "with the locked conflict");
      const ctx = await desktop();
      const page = await openPage(ctx, `${home}&folder=${ids.figures}`);
      try {
        const row = fileRow(page, "fig1.pdf");
        await row.waitFor({ timeout: 15000 });
        const items = await menuItems(page, row);
        for (const gone of ["Rename", "Move to folder", "Delete"]) assert(!items.includes(gone), `no ${gone} (${items.join(", ")})`);
        await page.goto(`${home}&folder=${link.folder_id}`);
        const dir = folderRow(page, "figures");
        await dir.waitFor({ timeout: 15000 });
        const folderItems = await menuItems(page, dir);
        for (const gone of ["Rename", "Delete", "New subfolder", "Sync with this computer…"]) {
          assert(!folderItems.includes(gone), `the directory's menu: no ${gone} (${folderItems.join(", ")})`);
        }
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("work-folders: a file unticked goes to Recently deleted with no Restore, its file untouched, and comes back ticked again", async () => {
      await user.api(`/api/pages/${ids.fig1}/comments`, { method: "POST", body: { block_id: ids.fig1, body: "Make the axes larger" } });
      const fig1 = path.join(paperDir, "figures", "fig1.pdf");
      const was = { bytes: fs.readFileSync(fig1), mtime: fs.statSync(fig1).mtimeMs };
      const ctx = await desktop();
      const page = await openPage(ctx, home);
      try {
        const choose = async () => {
          const w = folderRow(page, "Paper");
          await w.waitFor({ timeout: 15000 });
          await pick(page, w, "Choose files…");
          const dialog = page.getByRole("dialog", { name: "Choose files" });
          await treeFile(dialog, "notes.md").waitFor({ timeout: 15000 });
          return dialog;
        };
        let dialog = await choose();
        // figures/ is partly ticked (fig2 is not): a click ticks it whole, the next unticks it
        const figures = dialog.locator("summary", { has: page.locator("span:text-is('figures')") }).locator("input[type=checkbox]");
        await figures.check();
        await figures.uncheck();
        await until(async () => (await dialog.locator(".trackSummary").innerText()).includes("1 leaves: 1 with notes or open comments"),
          { timeout: 10000, what: "the summary says what leaves" });
        await dialog.getByRole("button", { name: "Save", exact: true }).click();
        await dialog.waitFor({ state: "detached", timeout: 15000 });
        const entry = async () => (await user.api("/api/trash")).pages.find((p) => p.id === ids.fig1);
        await until(entry, { timeout: 30000, what: "fig1's page in Recently deleted" });
        assertEq((await entry()).restorable, false, "a person cannot restore it");
        assert(fs.readFileSync(fig1).equals(was.bytes) && fs.statSync(fig1).mtimeMs === was.mtime, "its file is untouched");

        await page.getByRole("button", { name: "Recently deleted", exact: true }).click();
        const trash = page.getByRole("dialog", { name: "Recently deleted" });
        const row = trash.locator(`.aiProvRow[data-page="${ids.fig1}"]`);
        await row.waitFor();
        assertEq(await row.getByRole("button", { name: "Restore", exact: true }).count(), 0, "no Restore");
        await row.getByText("Comes back when its file is tracked again.").waitFor();
        await trash.getByRole("button", { name: "Close", exact: true }).click();

        dialog = await choose();
        await treeBox(dialog, "figures/fig1.pdf").check();
        await dialog.getByRole("button", { name: "Save", exact: true }).click();
        await dialog.waitFor({ state: "detached", timeout: 15000 });
        await until(async () => !(await entry()), { timeout: 30000, what: "fig1's page back" });
        const threads = await user.api(`/api/pages/${ids.fig1}/comments`);
        assert(JSON.stringify(threads).includes("Make the axes larger"), "with its comment");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("work-folders: a note renamed in Gamma is renamed on disk, and a page made in the work folder is written as a note", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx, at(ids.notes));
      try {
        const title = page.locator(".titleText");
        await title.waitFor({ timeout: 15000 });
        await title.click();
        await page.keyboard.press("ControlOrMeta+A");
        await page.keyboard.type("Plan");
        await page.keyboard.press("Enter");
        await until(async () => (await user.api(`/api/blocks/${ids.notes}`)).content === "Plan", { what: "the title saved" });
        await sync(link);
        await until(() => fs.existsSync(path.join(paperDir, "Plan.md")) && !fs.existsSync(path.join(paperDir, "notes.md")),
          { timeout: 20000, what: "Plan.md in place of notes.md" });

        await page.goto(`${home}&folder=${link.folder_id}`);
        await page.locator(".workList").waitFor({ timeout: 15000 });
        await page.locator(".folderNewBtn", { hasText: "New page" }).click();
        await page.locator(".titleEdit").waitFor({ timeout: 10000 });
        await page.keyboard.type("Draft");
        await page.keyboard.press("Enter");
        await until(async () => (await filesOf(link.folder_id)).some((p) => p.content === "Draft"), { what: "the new page is the work folder's" });
        await sync(link);
        await until(() => fs.existsSync(path.join(paperDir, "Draft.md")), { timeout: 20000, what: "Draft.md written" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("work-folders: Cancel of a work folder still choosing removes it, its folder and its .gamma", async () => {
      const other = path.join(disk, "Other");
      write(path.join(other, "a.md"), "A note.\n");
      const fresh = await make(other);
      const ctx = await desktop();
      const page = await openPage(ctx, `${home}&folder=${fresh.folder_id}&track=${fresh.id}`);
      try {
        const dialog = page.getByRole("dialog", { name: "Choose files" });
        await treeFile(dialog, "a.md").waitFor({ timeout: 15000 });
        await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
        await dialog.getByText("Cancel making this work folder?").waitFor();
        await dialog.getByRole("button", { name: "Cancel it", exact: true }).click();
        await dialog.waitFor({ state: "detached", timeout: 15000 });
        await until(async () => !(await user.api("/api/folder-links")).links.some((l) => l.id === fresh.id), { what: "the link is gone" });
        assert(!(await allFolders()).some((f) => f.id === fresh.folder_id), "its folder is gone");
        assert(!fs.existsSync(path.join(other, ".gamma")), "its .gamma is gone");
        assert(fs.existsSync(path.join(other, "a.md")), "the directory's own file stays");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("work-folders: a file the listing leaves out keeps its tab, its place in Recently viewed and its pin", async () => {
      const main = await user.api(`/api/blocks/${ids.main}`);
      const ctx = await desktop();
      const page = await openPage(ctx, at(ids.main));
      try {
        await page.locator(".titleText").waitFor({ timeout: 15000 });
        await page.reload();
        const tab = page.locator(".tabStrip .tab", { hasText: main.content }).first();
        await tab.waitFor({ timeout: 15000 });
        await tab.locator(".fileGlyph.pdf").waitFor({ timeout: 10000 });
        await page.getByRole("button", { name: "Home", exact: true }).click();
        await page.locator(".recentsCarousel .pageCard", { hasText: main.content }).first().waitFor({ timeout: 15000 });
        await user.api(`/api/blocks/${ids.main}`, { method: "PUT", body: { properties: { pinned: new Date().toISOString() } } });
        await page.reload();
        await page.locator(".pinnedStrip .pageCard", { hasText: main.content }).first().waitFor({ timeout: 15000 });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });
  } finally {
    await server.stop();
    fs.rmSync(disk, { recursive: true, force: true });
  }
}
