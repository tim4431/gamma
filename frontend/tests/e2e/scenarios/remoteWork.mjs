import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Account, Server, makePdf, wanted } from "../harness.mjs";

// A work folder of another server (docs/dev/work_folders.md, step 4b of
// docs/research/agent-collab.md): two Gammas, a NAS (a plain server) and a
// computer (the desktop app's own server: the vaults recipe of
// GAMMA_FOLDER_LINKS with no shell secret, so the API names paths as the shell
// would). On the NAS a shared workspace holds an empty folder "Paper" and an
// editor, bob; on the computer its own account makes, as the shell's
// makeRemoteWorkFolder does, a work folder of that folder with a write token
// minted on the NAS: the folder there carries the mark. The computer's own
// window, which says it is the desktop app, opens the tracking dialog at
// `?track=`: it says what is uploaded to the NAS and warns of a secrets file
// ticked, and its Save hands the window back to the shell (the page ask
// `tracked`) while the vault pushes the files there. On the NAS the folder
// lists them, an editor's rename, refiling or trash of a tracked file is
// refused, as is a vault's second mark inside it; then, the computer gone,
// the workspace's owner lets go of the folder from its menu: the files outside
// the library go to Recently deleted, the mark comes off, and what stays is
// the members' again.
const DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Gamma/1.0.0 "
  + "Chrome/138.0.0.0 Electron/38.0.0 Safari/537.36";
// a 1×1 PNG
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");

export async function remoteWorkScenarios(env) {
  const { browser, step, openPage, assert, assertEq, assertNoProblems, until } = env;
  if (!wanted("remote-work")) return;
  const disk = fs.mkdtempSync(path.join(os.tmpdir(), "gamma-e2e-remote-work-"));
  const nas = new Server();
  const computer = new Server({ env: { GAMMA_FOLDER_LINKS: "1", GAMMA_SHELL_SECRET: "" } });
  let computerUp = false;
  try {
    await nas.start();
    await computer.start();
    computerUp = true;
    for (const [name, pw] of [["rw-alice", "rw-alice-pw"], ["rw-bob", "rw-bob-pw"]]) await nas.manage("create-user", name, pw);
    const out = await nas.manage("create-workspace", "Thesis lab", "rw-alice", "shared");
    const wsId = (out.match(/workspace (\S+)/) || [])[1];
    assert(wsId, `shared workspace id from: ${out}`);
    await nas.manage("set-member", wsId, "rw-bob", "editor");
    const alice = await new Account(nas, "rw-alice", "rw-alice-pw").login();
    const bob = await new Account(nas, "rw-bob", "rw-bob-pw").login();
    alice.ws = bob.ws = wsId;
    // the computer's own account, which the desktop window is signed into
    await computer.manage("create-user", "rw-owner", "rw-owner-pw");
    const owner = await new Account(computer, "rw-owner", "rw-owner-pw").login();

    const folderId = await alice.folder("Paper");
    const shelf = await alice.folder("Shelf");
    const server = `127.0.0.1:${nas.port}`;
    const folderTree = async () => (await alice.api("/api/blocks/folders/subtree")).block.children;
    const paperFolder = async () => (await folderTree()).find((f) => f.id === folderId);
    const filesOf = async (folder) => (await alice.api(`/api/folders/${folder}/pages?limit=500`)).pages;
    const names = (pages) => pages.map((p) => p.properties?.file_name).sort();
    const writeToken = (name) => alice.api("/api/integrations/tokens", { method: "POST",
      body: { name, scope: "write", expires_in_days: 365 } });
    // written as an editor that finished a while ago, so a round takes it at once
    const write = (file, bytes) => {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, bytes);
      const before = new Date(Date.now() - 10_000);
      fs.utimesSync(file, before, before);
    };
    // A request of bob's to the NAS outside any page: a refusal is its answer, not a page's problem.
    const bobAsks = (pathname, method, body) => bob.api(pathname, { method, body, raw: true });

    // the paper directory on the computer
    const dir = path.join(disk, "Paper");
    write(path.join(dir, "main.tex"), "\\documentclass{article}\n\\begin{document}Hi\\end{document}\n");
    write(path.join(dir, "refs.bib"), "@article{a, title={A}}\n");
    write(path.join(dir, "notes.md"), "# notes\n\nWhat to write next.\n");
    write(path.join(dir, "paper.pdf"), Buffer.from(makePdf([["The draft of the paper"]])));
    write(path.join(dir, "figures", "plot.png"), PNG);
    write(path.join(dir, ".env"), "API_KEY=not-a-real-key\n");

    let link = null;
    let token = null;
    await step("remote-work: the computer makes a work folder of an empty NAS folder with a write token minted there, and the folder there carries the mark", async () => {
      // as the shell's makeRemoteWorkFolder: a write token on the NAS, the work folder on the host
      token = await writeToken("Gamma desktop: Paper synced vault");
      link = await owner.api("/api/folder-links", { method: "POST", body: { folder: folderId, path: dir, notes: true, pull: true,
        push: "everything", choose: true, remote_url: nas.base, workspace: wsId, token: token.token, token_id: token.id } });
      assertEq(link.remote_url, nas.base, "the vault keeps the NAS's folder");
      assertEq(link.folder_id, folderId, "that folder");
      assertEq(link.work, true, "a work folder");
      assertEq(link.choosing, true, "which waits for its files to be chosen");
      assert(link.status?.paused_at, `paused until then: ${JSON.stringify(link.status)}`);
      assert(!("token" in link), "the answer carries no token");
      assert(fs.existsSync(path.join(dir, ".gamma", "vault.json")), "the directory's marker is written");
      assertEq(JSON.stringify((await paperFolder()).properties?.vault), JSON.stringify({ pull: true, library: false }),
        "the NAS's folder carries the mark");
      assertEq((await filesOf(folderId)).length, 0, "no file there yet");
      const ran = await owner.api(`/api/folder-links/${link.id}/sync`, { method: "POST", raw: true });
      assertEq(ran.status, 409, "no round runs until its files are chosen");
      // a second work folder of the same NAS folder from another directory: refused here, as one vault pushes
      // into a folder (another computer's is refused by the NAS: the folder is a work folder already)
      const again = await writeToken("Gamma desktop: Paper again");
      const other = path.join(disk, "Paper again");
      fs.mkdirSync(other);
      const refused = await owner.api("/api/folder-links", { method: "POST", raw: true, body: { folder: folderId, path: other, notes: true,
        pull: true, push: "everything", choose: true, remote_url: nas.base, workspace: wsId, token: again.token, token_id: again.id } });
      const why = await refused.text();
      assertEq(refused.status, 400, `a second work folder of it is refused: ${why}`);
      assert(why.includes("only one vault pushes into a page"), `as the first pushes into it: ${why}`);
      assert(!fs.existsSync(path.join(other, ".gamma")), "the other directory gets no marker");
      assertEq((await owner.api("/api/folder-links")).links.length, 1, "the computer keeps the first alone");
      await alice.api(`/api/integrations/tokens/${again.id}`, { method: "DELETE" });
    });

    let ids = {};
    await step("remote-work: the computer's own window at ?track= says what goes to the NAS and warns of a secrets file; Save hands the window back and pushes the files there", async () => {
      const ctx = await owner.context(browser, { userAgent: DESKTOP_UA });
      // the page asks the shell's preload would pass on
      await ctx.addInitScript(() => {
        window.__asks = [];
        window.addEventListener("message", (e) => { if (e.data?.source === "gamma-app") window.__asks.push(e.data); });
      });
      const page = await openPage(ctx);
      const previews = [];
      page.on("response", async (r) => {
        if (r.url().includes(`/api/folder-links/${link.id}/selection/preview`) && r.ok()) previews.push(await r.json().catch(() => null));
      });
      try {
        await page.goto(`${computer.base}/?ws=${owner.ws}&track=${link.id}`);
        const dialog = page.getByRole("dialog", { name: "Choose files" });
        const treeFile = (rel) => dialog.locator(`.importTreeFile[title="${rel}"]`);
        const box = (rel) => treeFile(rel).locator("input[type=checkbox]");
        await treeFile("notes.md").waitFor({ timeout: 15000 });
        assert(!new URL(page.url()).searchParams.has("track"), "the hand-over leaves the address");
        assert(await box("notes.md").isChecked(), "a note is in the preset");
        assert(await box("paper.pdf").isChecked(), "a PDF is in the preset");
        for (const rel of ["main.tex", "figures/plot.png", ".env"]) await box(rel).check();
        await box("refs.bib").uncheck();
        const summary = dialog.locator(".trackSummary");
        const uploads = new RegExp(`^5 files, .+, are uploaded to ${server.replace(/\./g, "\\.")} and stored there, where every member of the workspace can read them$`);
        await until(async () => (await summary.locator("p").allInnerTexts()).some((s) => uploads.test(s.trim())),
          { timeout: 10000, what: `the summary's uploads line: ${await summary.innerText().catch(() => "")}` });
        const last = previews.at(-1);
        assertEq(last?.uploads?.files, 5, "the preview counts the five files uploaded");
        assertEq(JSON.stringify(last?.secrets), JSON.stringify([".env"]), "and the secrets file among them");
        const warning = summary.locator(".importWarning", { hasText: "may hold secrets" });
        assertEq((await warning.innerText()).trim(),
          `1 ticked file may hold secrets, and every member of the workspace on ${server} can read it:`, "the secrets warning");
        assertEq((await summary.locator("ul li strong").allInnerTexts()).map((s) => s.trim()).join("|"), ".env", "naming the file");
        // their PDFs and notes are library documents on the NAS too
        await dialog.locator(".trackLibrary input[type=checkbox]").check();
        await dialog.getByRole("button", { name: "Save", exact: true }).click();
        await dialog.waitFor({ state: "detached", timeout: 15000 });
        const asks = await page.evaluate(() => window.__asks.filter((a) => a.type === "tracked"));
        assertEq(JSON.stringify(asks.map(({ link: id, cancelled }) => ({ id, cancelled }))), JSON.stringify([{ id: link.id, cancelled: false }]),
          "the window is handed back to the shell, the vault kept");
        assertNoProblems(page);
      } finally { await ctx.close(); }

      // the round the Save started pushes the files into the NAS's folder
      const want = [".env", "main.tex", "notes.md", "paper.pdf"];
      await until(async () => {
        const now = await owner.api(`/api/folder-links/${link.id}`);
        if (now.status?.last_error) throw new Error(`the round failed: ${now.status.last_error}`);
        return JSON.stringify(names(await filesOf(folderId))) === JSON.stringify(want);
      }, { timeout: 45000, every: 500, what: `the NAS's folder lists ${want.join(", ")}` });
      const figures = await until(async () => (await paperFolder()).children.find((f) => f.content === "figures"),
        { timeout: 15000, what: "the figures directory's folder on the NAS" });
      await until(async () => JSON.stringify(names(await filesOf(figures.id))) === JSON.stringify(["plot.png"]),
        { timeout: 15000, what: "plot.png in it" });
      const now = await owner.api(`/api/folder-links/${link.id}`);
      assertEq(now.choosing, false, "the vault chooses no longer");
      assertEq(now.library, true, "its library switch is on");
      assertEq(JSON.stringify((await paperFolder()).properties?.vault), JSON.stringify({ pull: true, library: true }),
        "the mark on the NAS says so");
      const tree = (await owner.api(`/api/folder-links/${link.id}/tree?base=file`)).entries;
      assertEq(JSON.stringify(tree.filter((e) => !e.directory && e.selected).map((e) => e.path).sort()),
        JSON.stringify([".env", "figures/plot.png", "main.tex", "notes.md", "paper.pdf"]), "the choice reads back from its .gammaignore");
      const top = await filesOf(folderId);
      const byName = (name) => top.find((p) => p.properties?.file_name === name).id;
      ids = { tex: byName("main.tex"), env: byName(".env"), notes: byName("notes.md"), pdf: byName("paper.pdf"),
        png: (await filesOf(figures.id))[0].id, figures: figures.id };
      for (const p of top) assert(!JSON.stringify(p).includes(disk), `no path of the computer reaches the NAS: ${p.properties?.file_name}`);
    });

    await step("remote-work: on the NAS the folder lists the files, and an editor's rename, refiling or trash of a tracked file is refused, as a vault's mark inside it", async () => {
      const ctx = await alice.context(browser);
      try {
        const page = await openPage(ctx, `${nas.base}/?ws=${wsId}&folder=${folderId}`);
        const list = page.locator(".workList");
        await list.waitFor({ timeout: 15000 });
        await until(async () => (await list.locator(".fileRowName, .folderName").allInnerTexts()).map((s) => s.trim()).join("|")
          === "figures|.env|main.tex|notes.md|paper.pdf", { timeout: 15000, what: "the directory, then the files by name" });
        assertNoProblems(page);
      } finally { await ctx.close(); }

      const renamed = await bobAsks(`/api/blocks/${ids.tex}`, "PUT", { content: "draft.tex" });
      assertEq(renamed.status, 403, "renaming a tracked file is refused");
      assert((await renamed.text()).includes("locked"), "with the locked conflict");
      const refiled = await bobAsks(`/api/blocks/${ids.pdf}`, "PUT", { properties: { folders: [shelf] } });
      assertEq(refiled.status, 403, "refiling one is refused");
      assert((await refiled.text()).includes("locked"), "with the locked conflict");
      // (a note of a work folder that pulls follows Gamma: a member may send it to Recently deleted)
      const trashed = await bobAsks(`/api/blocks/${ids.tex}`, "DELETE");
      assertEq(trashed.status, 403, "sending a file that does not follow to Recently deleted is refused");
      assert((await trashed.text()).includes("locked"), "with the locked conflict");
      assertEq(names(await filesOf(folderId)).join("|"), ".env|main.tex|notes.md|paper.pdf", "the folder as it was");

      // a vault's mark (the computer's own token, as the vault's writer) of a folder inside it
      const marked = await fetch(`${nas.base}/api/pages/folders/ops`, { method: "POST",
        headers: { Authorization: `Bearer ${token.token}`, "X-Gamma-Workspace": wsId, "X-Gamma-Client": "disk", "Content-Type": "application/json" },
        body: JSON.stringify({ client: "disk", ops: [{ op: "set", id: ids.figures, props: { vault: { pull: true, library: false } } }] }) });
      assertEq(marked.status, 403, "a second mark inside a work folder is refused");
      const said = await marked.text();
      assert(said.includes("locked") && said.includes("never inside another work folder"), `with why: ${said}`);
      const figures = (await paperFolder()).children.find((f) => f.id === ids.figures);
      assertEq(figures.properties?.vault ?? null, null, "the folder inside carries no mark");
    });

    await step("remote-work: the computer gone, the workspace's owner lets go of the folder from its menu: the files outside the library go to Recently deleted, and what stays is the members' again", async () => {
      await computer.stop();
      computerUp = false;
      // an editor is offered no Let go, and may not let go
      const bctx = await bob.context(browser);
      try {
        const page = await openPage(bctx, `${nas.base}/?ws=${wsId}`);
        const row = page.locator(".fileList .folderRow", { has: page.locator(`.folderName:text-is("Paper")`) });
        await row.waitFor({ timeout: 15000 });
        await row.click({ button: "right" });
        const menu = page.locator(".ctxMenu");
        await menu.locator(".ctxMenuItem").first().waitFor();
        const items = (await menu.locator(".ctxMenuItem").allInnerTexts()).map((s) => s.trim());
        assert(!items.some((s) => s.startsWith("Let go")), `no Let go for an editor: ${items.join(", ")}`);
        await page.keyboard.press("Escape");
        assertNoProblems(page);
      } finally { await bctx.close(); }
      const refused = await bobAsks(`/api/folders/${folderId}/let-go`, "POST");
      assertEq(refused.status, 403, "an editor's let-go is refused");

      const ctx = await alice.context(browser);
      try {
        const page = await openPage(ctx, `${nas.base}/?ws=${wsId}`);
        const row = page.locator(".fileList .folderRow", { has: page.locator(`.folderName:text-is("Paper")`) });
        await row.waitFor({ timeout: 15000 });
        await row.click({ button: "right" });
        await page.locator(".ctxMenu .ctxMenuItem", { hasText: "Let go of this work folder…" }).click();
        const confirm = page.locator(".confirmModal", { hasText: "Let go of this work folder?" });
        await confirm.waitFor();
        assert((await confirm.innerText()).includes("The files outside your library go to Recently deleted; your notes and papers stay in the folder."),
          "the box says what happens to the files");
        const answered = page.waitForResponse((r) => r.url().endsWith(`/api/folders/${folderId}/let-go`));
        await confirm.getByRole("button", { name: "Let go", exact: true }).click();
        const answer = await (await answered).json();
        assertEq(JSON.stringify({ kept: answer.kept, trashed: answer.trashed }), JSON.stringify({ kept: 2, trashed: 3 }),
          "the PDF and the note kept, the other three files sent");
        await confirm.waitFor({ state: "detached" });
        await page.getByText("Let go of the work folder: 3 files went to Recently deleted.").first().waitFor({ timeout: 10000 });
        assertNoProblems(page);
      } finally { await ctx.close(); }

      const folder = await paperFolder();
      assert(folder, "the folder stays, holding the papers");
      assertEq(folder.properties?.vault ?? null, null, "its mark is gone");
      assert(!folder.children.some((f) => f.id === ids.figures), "the directory left empty is gone");
      const trash = (await alice.api("/api/trash")).pages.map((p) => p.id);
      for (const [name, id] of [["main.tex", ids.tex], [".env", ids.env], ["plot.png", ids.png]]) {
        assert(trash.includes(id), `${name}, in no library, is in Recently deleted`);
      }
      // a library folder again: the library's listing names what is filed in it
      const filed = (await alice.api("/api/blocks/root/children")).children
        .filter((p) => (p.properties?.folders || []).includes(folderId)).map((p) => p.id).sort();
      assertEq(JSON.stringify(filed), JSON.stringify([ids.notes, ids.pdf].sort()), "the note and the PDF stay in the folder");
      for (const id of [ids.notes, ids.pdf]) {
        const props = (await alice.api(`/api/blocks/${id}`)).properties;
        assert(!props.work_folder && !props.file_name, `the page is no file any more: ${JSON.stringify(props)}`);
      }
      // bob's refiling refused before is his now, and a rename too
      const refiled = await bobAsks(`/api/blocks/${ids.pdf}`, "PUT", { properties: { folders: [shelf] } });
      assertEq(refiled.status, 200, `refiling what stays: ${await refiled.text()}`);
      const renamed = await bobAsks(`/api/blocks/${ids.notes}`, "PUT", { content: "Next steps" });
      assertEq(renamed.status, 200, `renaming it: ${await renamed.text()}`);
      assertEq((await alice.api(`/api/blocks/${ids.notes}`)).content, "Next steps", "the rename is kept");
    });
  } finally {
    if (computerUp) await computer.stop();
    await nas.stop();
    fs.rmSync(disk, { recursive: true, force: true });
  }
}
