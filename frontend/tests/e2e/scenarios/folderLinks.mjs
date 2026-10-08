// Folders on disk (docs/dev/folder_sync.md "Links kept by the server"):
// Settings → Workspaces → Folders on disk. A folder with a paper that has a
// note is linked from the dialog; the server writes it under its folders
// root (the harness's data directory), the row reports the round and Sync
// runs another; Remove link and files takes the directory back.
import fs from "node:fs";
import path from "node:path";
import { Account, wanted } from "../harness.mjs";

export async function folderLinkScenarios(env) {
  const { server, browser, step, openPage, assert, until } = env;
  if (!wanted("folder-links")) return;
  server.manage("create-user", "disk-user", "disk-pw");
  const user = await new Account(server, "disk-user", "disk-pw").login();
  await user.api("/api/pages/folders/ops", { method: "POST", body: { client: "e2e", ops: [
    { op: "insert", id: "diskfolder1", parent: "folders", position: "a0", content: "Disk lab" }] } });
  const paper = await user.api("/api/pages", { method: "POST", body: { title: "Disk paper" } });
  await user.api(`/api/blocks/${paper.id}`, { method: "PUT", body: { properties: { folders: ["diskfolder1"] } } });
  await user.api(`/api/pages/${paper.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
    { op: "insert", id: "diskblk1", parent: paper.id, position: "a0", content: "a note kept on disk" }] } });
  const dir = path.join(server.dataDir, "folders", "Disk lab");

  await step("folder links: keep a folder on disk from Settings, see it written, sync it, remove it with its files", async () => {
    const ctx = await user.context(browser);
    try {
      const page = await openPage(ctx, server.base);
      await page.waitForSelector(".folderNewBtn");
      await page.locator('[data-guide="header.account"]').click();
      await page.getByRole("button", { name: "Settings…", exact: true }).click();
      await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor();
      await page.getByRole("navigation", { name: "Settings categories" }).getByRole("button", { name: "Workspaces", exact: true }).click();
      await page.getByText("No folders on disk yet.", { exact: true }).waitFor();

      await page.getByRole("button", { name: "Keep a folder on disk", exact: true }).click();
      const dlg = page.getByRole("dialog", { name: "Keep a folder on disk", exact: true });
      await dlg.waitFor();
      // the first folder of the tree is preselected; the directory takes the folder's own name
      await dlg.getByRole("button", { name: "Keep on disk", exact: true }).click();
      const row = page.locator("[data-folder-link]");
      await row.waitFor();
      assert((await row.textContent()).includes("Disk lab"), "the row names the folder");
      assert((await row.locator(".aiProvDesc").first().textContent()).endsWith("Disk lab"), "the row shows the directory");
      await until(() => row.locator(".aiProvDesc").last().textContent().then((s) => /up to date/.test(s)),
        { timeout: 20000, what: "the first round reports" });
      const md = path.join(dir, "Disk paper.md");
      assert(fs.existsSync(md), `the note file is written: ${md}`);
      assert(fs.readFileSync(md, "utf8").includes("a note kept on disk"), "the note's text is in the file");
      assert(fs.readFileSync(md, "utf8").startsWith(`---\ngamma_id: ${paper.id}\n`), "the file names its page");

      // Sync runs a round inline: nothing changed
      await row.getByRole("button", { name: "Sync", exact: true }).click();
      await until(() => row.locator(".aiProvDesc").last().textContent().then((s) => /nothing had changed/.test(s)),
        { timeout: 20000, what: "the round found nothing to do" });

      // Remove link and files takes the directory back
      await row.getByRole("button", { name: "More", exact: true }).click();
      await page.locator(".ctxMenuItem", { hasText: "Remove link and files" }).click();
      const confirm = page.locator(".confirmModal", { hasText: "Takes back what the sync wrote" });
      await confirm.waitFor();
      await confirm.getByRole("button", { name: "Remove link and files", exact: true }).click();
      await page.getByText("No folders on disk yet.", { exact: true }).waitFor();
      await until(() => Promise.resolve(!fs.existsSync(dir)), { timeout: 10000, what: "the directory is gone" });
    } finally {
      await ctx.close();
    }
  });
}
