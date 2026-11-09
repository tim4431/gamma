import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Account, Server, makePdf, wanted } from "../harness.mjs";
import { row } from "./notes.mjs";

// Synced vaults and PDFs read where they are, as a page of the desktop app
// shows them (docs/research/vaults.md): a backend that keeps vaults
// (GAMMA_FOLDER_LINKS, and no shell secret, so the API names paths as the
// shell would), a browser that says it is the desktop app (an Electron user
// agent: IS_DESKTOP), and real directories on disk made vaults through the
// API. The page's chip and its card, Edit the file and its 409, a conflict
// the file won and its merge chip, the notes lock of a vault that does not
// pull, Add to library… from Opened files, from the Pinned and Recent strips
// and a tab, and into another workspace, its Another server… (a message to
// the shell), and the folder and Document menu items, whose messages to the
// shell an init script records.
const DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Gamma/1.0.0 "
  + "Chrome/138.0.0.0 Electron/38.0.0 Safari/537.36";

export async function vaultScenarios(env) {
  const { browser, step, openPage, assert, assertEq, assertNoProblems, until } = env;
  if (!wanted("vaults")) return;
  const server = new Server({ env: { GAMMA_FOLDER_LINKS: "1", GAMMA_SHELL_SECRET: "" } });
  const disk = fs.mkdtempSync(path.join(os.tmpdir(), "gamma-e2e-vaults-"));
  try {
    await server.start();
    await server.manage("create-user", "vault-user", "vault-pw");
    const user = await new Account(server, "vault-user", "vault-pw").login();
    const at = (id) => `${server.base}/?page=${id}&ws=${user.ws}`;
    const subtree = async (id) => (await user.api(`/api/blocks/${id}/subtree`)).block;
    // a page's blocks at every depth (a heading's paragraphs are its children)
    const blocksOf = async (id) => {
      const all = [];
      const walk = (b) => { for (const c of b.children || []) { all.push(c); walk(c); } };
      walk(await subtree(id));
      return all;
    };
    const texts = async (id) => (await blocksOf(id)).map((b) => b.content);
    const pageTitled = async (title) => (await user.api("/api/blocks/root/children")).children.find((p) => (p.content || p.title) === title);
    // A file written as an editor that finished a while ago would leave it: a
    // round takes a note only once it has settled (vault_sync SETTLE_NOTE_S).
    const write = (file, text) => {
      fs.writeFileSync(file, text);
      const before = new Date(Date.now() - 10_000);
      fs.utimesSync(file, before, before);
    };
    // A directory made a vault, its first round run: the directory and the link.
    const vaultOf = async (name, files, { pull = true, push = "everything" } = {}) => {
      const dir = path.join(disk, name);
      fs.mkdirSync(dir, { recursive: true });
      for (const [file, text] of Object.entries(files)) write(path.join(dir, file), text);
      const link = await user.api("/api/folder-links", { method: "POST", body: { path: dir, new_folder: true, pull, push } });
      await user.api(`/api/folder-links/${link.id}/sync?wait=1`, { method: "POST" });
      return { dir, link: await user.api(`/api/folder-links/${link.id}`) };
    };
    // A desktop app's window: its user agent, and the messages its page posts to the shell.
    const desktop = async () => {
      const ctx = await user.context(browser, { userAgent: DESKTOP_UA });
      await ctx.addInitScript(() => {
        window.shellMessages = [];
        window.addEventListener("message", (e) => {
          if (e.data?.source === "gamma-app" && e.data.type !== "connector-hello") window.shellMessages.push(e.data);
        });
      });
      return ctx;
    };
    const posted = (page) => page.evaluate(() => window.shellMessages);

    const papers = await vaultOf("Papers", { "Attention.md": "# Attention\n\nThe first note.\n\n- a point\n" });
    const note = path.join(papers.dir, "Attention.md");
    const paper = await until(() => pageTitled("Attention"), { timeout: 15000, what: "the vault's note became a page" });

    await step("vaults: a synced page shows its chip, and its card says where the file is and shows it in its folder", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx, at(paper.id));
      try {
        const chip = page.locator(".vaultChip");
        await chip.waitFor({ timeout: 15000 });
        assertEq((await chip.innerText()).trim(), "Synced: Attention.md", "the chip names the file");
        assert((await chip.getAttribute("title")).includes(note), "its title the whole path");
        await chip.click();
        const card = page.locator(".vaultPopover");
        await card.waitFor();
        await card.getByText(note, { exact: true }).waitFor();
        await card.getByText("Pull: on · Push: everything", { exact: true }).waitFor();
        await card.locator(".ctxMenuItem", { hasText: "Edit the file" }).waitFor();
        assertEq(await card.locator(".ctxMenuItem", { hasText: "Add to library…" }).count(), 0, "a vault's page is in the library already");
        await card.locator(".ctxMenuItem", { hasText: "Show in folder" }).click();
        await card.waitFor({ state: "detached" });
        const sent = await posted(page);
        assertEq(JSON.stringify(sent.map(({ type, ws, page: p }) => ({ type, ws, page: p }))),
          JSON.stringify([{ type: "reveal-on-disk", ws: user.ws, page: paper.id }]), "Show in folder asks the shell");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("vaults: Edit the file writes the file, the page takes it, and a file changed meanwhile is offered back", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx, at(paper.id));
      try {
        const editFile = async () => {
          await page.locator(".vaultChip").click();
          await page.locator(".vaultPopover .ctxMenuItem", { hasText: "Edit the file" }).click();
          const dialog = page.getByRole("dialog", { name: "Edit the file" });
          await dialog.locator(".fileSourceEditor .cm-content").waitFor();
          await until(async () => (await dialog.locator(".cm-content").innerText()).includes("The first note."), { what: "the file's text in the editor" });
          return dialog;
        };
        let dialog = await editFile();
        await dialog.locator(".fileSourcePath").getByText(note, { exact: true }).waitFor();
        await dialog.locator(".cm-content").click();
        await page.keyboard.press("Control+End");
        await page.keyboard.type("\nA line from the editor.");
        await dialog.getByRole("button", { name: "Save", exact: true }).click();
        await dialog.waitFor({ state: "detached" });
        assert(fs.readFileSync(note, "utf8").includes("- a point\n\nA line from the editor."), "the file holds what was saved");
        await until(async () => (await texts(paper.id)).includes("A line from the editor."), { timeout: 15000, what: "the page took the file" });
        await row(page, "A line from the editor.").waitFor({ timeout: 10000 });

        // the file changes on disk while the editor is open: the save is refused, and its new text offered
        dialog = await editFile();
        fs.appendFileSync(note, "\n\nChanged on disk.\n");
        await dialog.locator(".cm-content").click();
        await page.keyboard.press("Control+End");
        await page.keyboard.type(" (unsaved)");
        await dialog.getByRole("button", { name: "Save", exact: true }).click();
        await dialog.locator(".fileSourceMoved").waitFor();
        assert(!fs.readFileSync(note, "utf8").includes("(unsaved)"), "nothing was written over the newer file");
        await dialog.getByRole("button", { name: "Load its new text", exact: true }).click();
        await until(async () => (await dialog.locator(".cm-content").innerText()).includes("Changed on disk."), { what: "the file's new text" });
        assertEq(await dialog.locator(".fileSourceMoved").count(), 0, "the question goes with the new text");
        await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
        await dialog.waitFor({ state: "detached" });
        await until(async () => (await texts(paper.id)).includes("Changed on disk."), { timeout: 15000, what: "the page took the change made on disk" });
        assertNoProblems(page, [/PUT \/api\/folder-links\/page\/[^ ]+\/file -> 409/]);
      } finally { await ctx.close(); }
    });

    await step("vaults: a block changed in the file and in Gamma gets the merge chip with the file's and Gamma's sides", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx, at(paper.id));
      try {
        await row(page, "The first note.").waitFor({ timeout: 15000 });
        const first = (await blocksOf(paper.id)).find((b) => b.content === "The first note.");
        assert(first, "the note's block");
        // paused, so no round runs between the two edits; a sync asked for still runs
        await user.api(`/api/folder-links/${papers.link.id}`, { method: "PATCH", body: { paused: true } });
        await user.api(`/api/pages/${paper.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
          { op: "set", id: first.id, content: "The second note." }] } });
        write(note, fs.readFileSync(note, "utf8").replace("The first note.", "The third note."));
        await user.api(`/api/folder-links/${papers.link.id}/sync?wait=1`, { method: "POST" });
        const open = (await user.api(`/api/folder-links/${papers.link.id}/conflicts?page=${paper.id}`)).conflicts;
        assertEq(open.map((c) => c.kind).join(","), "file_won", "the file won, and the clash is kept");
        await until(async () => (await texts(paper.id)).includes("The third note."),
          { what: "the block took the file's text" });

        await until(() => page.locator(".mergeChip").count().then((n) => n === 1), { timeout: 15000, what: "the block carries a merge chip" });
        await page.locator(".mergeChip").click();
        const merge = page.getByRole("dialog", { name: "Merge", exact: true });
        await merge.getByText("Changed in the file and in Gamma", { exact: true }).waitFor();
        assertEq(await merge.locator(".mergeVersion").count(), 2, "the two sides, no merged text");
        assertEq((await merge.locator(".mergeSide").allInnerTexts()).map((s) => s.trim()).join("|"), "Gamma|This file", "Gamma and this file");
        assertEq(await merge.locator(".mergeVersion.theirs .uiTag").innerText(), "in the block", "the file's text is in the block");
        await merge.locator(".mergeVersion.mine .mergePick").check();
        await merge.getByText("Puts Gamma's text back into the block; the next pull writes it to the file", { exact: true }).waitFor();
        await merge.getByRole("button", { name: "Apply", exact: true }).click();
        await until(async () => (await texts(paper.id)).includes("The second note."),
          { what: "Gamma's text back in the block" });
        assertEq((await user.api(`/api/folder-links/${papers.link.id}/conflicts?page=${paper.id}`)).conflicts.length, 0, "resolved");
        await page.locator(".mergeChip").waitFor({ state: "detached", timeout: 10000 });
        await user.api(`/api/folder-links/${papers.link.id}`, { method: "PATCH", body: { paused: false } });
        await until(() => fs.readFileSync(note, "utf8").includes("The second note."), { timeout: 20000, what: "the next pull wrote it to the file" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("vaults: in a vault that does not pull, a note has no editor and a highlight keeps its own", async () => {
      await vaultOf("Locked", { "Locked note.md": "Written in the file.\n" }, { pull: false });
      const locked = await until(() => pageTitled("Locked note"), { timeout: 15000, what: "the locked vault's page" });
      await user.api("/api/blocks", { method: "POST", body: { parent_id: locked.id, content: "a highlight's own note",
        properties: { quote: "a quoted passage", pdf_position: { pageNumber: 1 } } } });
      const ctx = await desktop();
      const page = await openPage(ctx, at(locked.id));
      try {
        await page.locator(".vaultChip").waitFor({ timeout: 15000 });
        await page.locator(".vaultChip").click();
        await page.locator(".vaultPopover").getByText(/does not pull: the notes are written in the file/).waitFor();
        await page.locator(".vaultChip").click(); // the chip shuts its card
        await page.locator(".vaultPopover").waitFor({ state: "detached" });
        const plain = row(page, "Written in the file.");
        await plain.locator(".blockBody").click();
        await page.waitForTimeout(400);
        assertEq(await page.locator(".blockEditorCm").count(), 0, "the note opens no editor");
        assertEq(await page.locator(".notesTail").count(), 0, "and no new note is started below");
        const highlight = row(page, "a highlight's own note");
        assertEq(await page.locator(".sortableBlockWrap", { has: highlight }).first().locator(".addHandle").count(), 0,
          "the highlight's row has no + for a new note");
        const before = (await blocksOf(locked.id)).length;
        await highlight.locator(".blockBody").click();
        await page.locator(".blockEditorCm .cm-content").waitFor({ timeout: 5000 });
        assert((await page.locator(".blockEditorCm .cm-content").innerText()).includes("a highlight's own note"), "the highlight's note is edited");
        // the new-note Enter makes no note, and the arrow up does not leave for the note above, which has no editor
        await page.keyboard.press("End");
        await page.keyboard.press("Enter");
        await page.keyboard.press("ArrowUp");
        await page.waitForTimeout(400);
        assertEq(await page.locator(".blockEditorCm").count(), 1, "the highlight's editor stays open");
        assert((await page.locator(".blockEditorCm .cm-content").innerText()).includes("a highlight's own note"), "on the highlight");
        await page.keyboard.press("Escape");
        await page.waitForTimeout(400);
        assertEq((await blocksOf(locked.id)).length, before, "no note was made");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("vaults: a PDF opened from this computer is on this computer, and Add to library… from Opened files files it", async () => {
      const loose = path.join(disk, "loose");
      fs.mkdirSync(loose, { recursive: true });
      const file = path.join(loose, "Loose paper.pdf");
      write(file, Buffer.from(makePdf([["A paper read where it lies"]])));
      const opened = await user.api("/api/folder-links/open", { method: "POST", body: { path: file } });
      assert(opened.created && opened.workspace === user.ws, "a new page, here");
      const shelf = await user.folder("Read later");
      const ctx = await desktop();
      const page = await openPage(ctx, at(opened.page));
      try {
        const chip = page.locator(".vaultChip");
        await chip.waitFor({ timeout: 15000 });
        assertEq((await chip.innerText()).trim(), "On this computer", "the chip says where it is read");
        // nothing in the Document menu replaces the file it reads
        await page.click("button[aria-label='Document']");
        const doc = page.locator(".attachPopover");
        await doc.locator(".ctxMenuItem", { hasText: "Download the PDF" }).waitFor();
        for (const gone of ["Replace the PDF…", "Push from a file on this computer…", "Detach the PDF…"]) {
          assertEq(await doc.locator(".ctxMenuItem", { hasText: gone }).count(), 0, `no ${gone}`);
        }
        await page.click("button[aria-label='Document']");
        // the tab menu offers Add to library… in place of the moves
        await page.locator(".tabStrip .tab", { hasText: "Loose paper" }).first().click({ button: "right" });
        await page.locator(".ctxMenu .ctxMenuItem", { hasText: "Add to library…" }).waitFor();
        assertEq(await page.locator(".ctxMenu .ctxMenuItem", { hasText: "Move to library" }).count(), 0, "no Move to library");
        assertEq(await page.locator(".ctxMenu .ctxMenuItem", { hasText: "Move into page…" }).count(), 0, "no Move into page…");
        await page.locator(".ctxMenu .ctxMenuItem", { hasText: "Add to library…" }).click();
        const dialog = page.locator(".folderPicker");
        await dialog.waitFor();
        await dialog.locator("input[data-find]").fill("Read");
        await dialog.locator(".moveBlockList .ctxMenuItem", { hasText: "Read later" }).click();
        await dialog.waitFor({ state: "detached" });
        await until(async () => {
          const props = (await subtree(opened.page)).properties || {};
          return !props.parent_page && !props.linked_file && JSON.stringify(props.folders) === JSON.stringify([shelf]);
        }, { what: "out of Opened files, stored and filed" });
        await chip.waitFor({ state: "detached", timeout: 10000 });
        assertEq((await user.api(`/api/folder-links/page/${opened.page}`)).reader, null, "it reads no file any more");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    // A PDF of this computer opened where it is (POST /open): its page under Opened files.
    const openFile = async (name, text) => {
      const dir = path.join(disk, "loose");
      fs.mkdirSync(dir, { recursive: true });
      const file = path.join(dir, `${name}.pdf`);
      write(file, Buffer.from(makePdf([[text]])));
      return user.api("/api/folder-links/open", { method: "POST", body: { path: file } });
    };
    // The context menu a right-click opens: what it offers of the moves.
    const offers = async (page, target) => {
      await target.click({ button: "right" });
      const menu = page.locator(".ctxMenu");
      await menu.locator(".ctxMenuItem", { hasText: "Add to library…" }).waitFor();
      const items = (await menu.locator(".ctxMenuItem").allInnerTexts()).map((s) => s.trim());
      await page.keyboard.press("Escape");
      await menu.waitFor({ state: "detached" });
      return items;
    };

    await step("vaults: an opened PDF in the Pinned and Recent strips and in a tab not shown offers Add to library…, not the moves or Duplicate; selected with a library page, neither", async () => {
      const opened = await openFile("Pinned paper", "A pinned paper read where it lies");
      const up = await user.upload("/api/uploads", makePdf([["A pinned paper of the library"]]), "pinned-library.pdf", "application/pdf");
      // its page, titled after the file
      const stored = await user.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST",
        body: { default_title: "pinned-library.pdf", source_url: up.source_url, original_filename: "pinned-library.pdf" } });
      for (const id of [opened.page, stored.id]) {
        await user.api(`/api/blocks/${id}`, { method: "PUT", body: { properties: { pinned: new Date().toISOString() } } });
      }
      const ctx = await desktop();
      const page = await openPage(ctx, at(opened.page));
      try {
        await page.locator(".vaultChip").waitFor({ timeout: 15000 });
        await page.getByRole("button", { name: "Home", exact: true }).click();
        await page.locator(".fileList").waitFor();
        const none = (items, where) => {
          for (const gone of ["Move into page…", "Move to library", "Move to folder", "Duplicate"]) {
            assert(!items.includes(gone), `${where}: no ${gone} (${items.join(", ")})`);
          }
        };
        none(await offers(page, page.locator(".pinnedStrip .pageCard", { hasText: "Pinned paper" })), "the Pinned strip");
        none(await offers(page, page.locator(".recentsCarousel .pageCard", { hasText: "Pinned paper" })), "Recently viewed");
        none(await offers(page, page.locator(".tabStrip .tab", { hasText: "Pinned paper" }).first()), "its tab, the home view shown");
        await page.locator(".pinnedStrip .pageCard", { hasText: "Pinned paper" }).click({ button: "right" });
        await page.locator(".ctxMenu .ctxMenuItem", { hasText: "Add to library…" }).click();
        await page.locator(".folderPicker").waitFor();
        assertEq(await page.locator(".folderPicker .folderPickerWorkspace").count(), 0, "one workspace to write: no choice of it");
        await page.keyboard.press("Escape");
        await page.locator(".folderPicker").waitFor({ state: "detached" });

        // selected with a page of the library: neither the moves nor Add to library…
        // (the library page alone offers Move into page…, so the check fails if the selection did not take)
        const card = (title) => page.locator(".pinnedStrip .pageCard", { hasText: title });
        await card("Pinned paper").click();
        await card("pinned-library.pdf").click({ modifiers: ["Control"] });
        await card("pinned-library.pdf").click({ button: "right" });
        const menu = page.locator(".ctxMenu");
        await menu.locator(".ctxMenuItem", { hasText: "Unpin" }).first().waitFor();
        const items = (await menu.locator(".ctxMenuItem").allInnerTexts()).map((s) => s.trim());
        for (const gone of ["Move into page…", "Move to library", "Add to library…"]) {
          assert(!items.includes(gone), `a mixed selection: no ${gone} (${items.join(", ")})`);
        }
        await page.keyboard.press("Escape");
        await menu.waitFor({ state: "detached" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("vaults: Add to library… takes an opened PDF to another workspace's folder and opens it there; its Another server… asks the shell for another server's", async () => {
      const shelfWs = await user.api("/api/workspaces", { method: "POST", body: { name: "Second shelf" } });
      const there = new Account(server, "vault-user", "vault-pw");
      there.ws = shelfWs.id;
      await there.login();
      const kept = await there.folder("Kept papers");
      const opened = await openFile("Carried paper", "A paper carried to another workspace");
      const ctx = await desktop();
      const page = await openPage(ctx, at(opened.page));
      try {
        const chip = page.locator(".vaultChip");
        await chip.waitFor({ timeout: 15000 });
        const dialog = page.locator(".folderPicker");
        await chip.click();
        assertEq(await page.locator(".vaultPopover .ctxMenuItem").allInnerTexts().then((l) => l.map((s) => s.trim()).join("|")),
          "Show in folder|Add to library…", "the card's actions: another server's library is a choice of Add to library…");
        await page.locator(".vaultPopover .ctxMenuItem", { hasText: "Add to library…" }).click();
        await dialog.waitFor();
        await dialog.getByRole("button", { name: "Another server…" }).click();
        await dialog.waitFor({ state: "detached" });
        await until(async () => (await posted(page)).some((m) => m.type === "carry-to-server"), { what: "the message went" });
        assertEq(JSON.stringify((await posted(page)).map(({ source, type, ws, page: p }) => ({ source, type, ws, page: p }))),
          JSON.stringify([{ source: "gamma-app", type: "carry-to-server", ws: user.ws, page: opened.page }]), "the shell is asked to carry it");

        await chip.click();
        await page.locator(".vaultPopover .ctxMenuItem", { hasText: "Add to library…" }).click();
        await dialog.waitFor();
        await dialog.locator(".folderPickerWorkspace .uiSelectBtn").click();
        await page.locator(".uiSelectMenu .ctxMenuItem", { hasText: "Second shelf" }).click();
        await dialog.getByText("It leaves this workspace for Second shelf, and opens there.").waitFor();
        await dialog.locator(".moveBlockList .ctxMenuItem", { hasText: "Kept papers" }).click();
        await until(() => {
          const u = new URL(page.url());
          return u.searchParams.get("ws") === shelfWs.id && u.searchParams.get("block") === opened.page;
        }, { timeout: 20000, what: "the tab opens the page in the other workspace" });
        await page.locator(".tabStrip .tab", { hasText: "Carried paper" }).first().waitFor({ timeout: 15000 });
        const props = (await there.api(`/api/blocks/${opened.page}/subtree`)).block.properties || {};
        assert(!props.parent_page && !props.linked_file, "stored, out of Opened files");
        assertEq(JSON.stringify(props.folders), JSON.stringify([kept]), "filed in the folder picked there");
        const left = await user.api(`/api/blocks/${opened.page}/subtree`, { raw: true });
        assertEq(left.status, 404, "the opened page here is gone");
        // the page's socket may say it went before the add's answer turns the tab: one read of it here finds none
        assertNoProblems(page, [new RegExp(`GET /api/blocks/${opened.page}/subtree -> 404`)]);
      } finally { await ctx.close(); }
    });

    await step("vaults: the folder menu's Sync with this computer… and the Document menu's Push from a file… ask the shell", async () => {
      const up = await user.upload("/api/uploads", makePdf([["A paper of the library"]]), "library.pdf", "application/pdf");
      const doc = await user.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST",
        body: { default_title: "Library paper", source_url: up.source_url, original_filename: "library.pdf" } });
      const ctx = await desktop();
      const page = await openPage(ctx, `${server.base}/?ws=${user.ws}`);
      try {
        const folder = page.locator(".fileList .folderRow", { hasText: "Read later" });
        await folder.waitFor({ timeout: 15000 });
        await folder.click({ button: "right" });
        await page.locator(".ctxMenu .ctxMenuItem", { hasText: "Sync with this computer…" }).click();
        const shelf = await user.folder("Read later");
        // the names from before synced vaults, which a desktop app released before them also passes on
        await until(async () => (await posted(page)).some((m) => m.type === "keep-folder-on-disk"), { what: "the message went" });
        assertEq(JSON.stringify((await posted(page)).map(({ type, ws, folder: f }) => ({ type, ws, folder: f }))),
          JSON.stringify([{ type: "keep-folder-on-disk", ws: user.ws, folder: shelf }]), "the folder's message");

        await page.goto(at(doc.id));
        await page.click("button[aria-label='Document']");
        await page.locator(".attachPopover .ctxMenuItem", { hasText: "Replace the PDF…" }).waitFor();
        const push = page.locator('.attachPopover [data-guide="page.pushFile"]');
        assertEq((await push.innerText()).trim(), "Push from a file on this computer…", "the item and its guide anchor");
        await push.click();
        await until(async () => (await posted(page)).some((m) => m.type === "follow-file-on-disk"), { what: "the message went" });
        assertEq(JSON.stringify((await posted(page)).map(({ type, ws, page: p }) => ({ type, ws, page: p }))),
          JSON.stringify([{ type: "follow-file-on-disk", ws: user.ws, page: doc.id }]), "the page's message");
        assertEq(await page.locator(".vaultChip").count(), 0, "a library page of no vault has no chip");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });
  } finally {
    await server.stop();
    fs.rmSync(disk, { recursive: true, force: true });
  }
}
