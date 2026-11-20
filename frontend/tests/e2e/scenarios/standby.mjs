import fs from "node:fs";
import { Account, Server, wanted } from "../harness.mjs";

// The page a server shows while it cannot open its library
// (auth/StandbyPage.jsx; backend gamma/standby.py): the iPad app's own
// server (GAMMA_UPDATES=app) on a library a newer Gamma stamped. It opens
// on that page, not the app: its update button opens TestFlight, and its
// admin, signed in as the iPad app signs in, saves every personal
// workspace with Settings' Export all, followed as Settings follows an
// export and downloaded by itself once it is made: one zip of each
// workspace's export zip. Nothing failed but the refusal the page reads.
const STAMP = [
  "import os, sqlite3",
  "conn = sqlite3.connect(os.path.join(os.environ['GAMMA_DATA_DIR'], 'users.db'))",
  "conn.execute(f\"PRAGMA user_version = {conn.execute('PRAGMA user_version').fetchone()[0] + 1}\")",
  "conn.commit()",
].join("\n");
// the server's config answering its guidance, and the browser's line for that answer
const REFUSAL = ["GET /api/server-config -> 503", /status of 503/];

export async function standbyScenarios({ browser, step, openPage, assert, assertEq, assertNoProblems }) {
  if (!wanted("standby")) return;
  const ipad = new Server({ env: { GAMMA_UPDATES: "app" } });
  try {
    await ipad.start();
    await ipad.manage("create-user", "sb-owner", "sb-owner-pw");
    await ipad.manage("set-admin", "sb-owner", "on");
    const owner = await new Account(ipad, "sb-owner", "sb-owner-pw").login();
    await owner.api("/api/pages", { method: "POST", body: { title: "Written by the newer Gamma" } });
    await ipad.halt();
    await ipad.python("-c", STAMP);
    await ipad.start({ port: ipad.port, setup: false });

    await step("standby: a library a newer Gamma wrote opens on the update page, TestFlight's button, and its admin saves every workspace with Export all", async () => {
      const ctx = await browser.newContext();
      const page = await openPage(ctx, `${ipad.base}/`);
      await page.locator(".loginHeadline", { hasText: "Gamma needs an update to open your library" }).waitFor();
      assertEq(await page.locator(".loginCard a.uiBtn.primary").getAttribute("href"), "itms-beta://",
        "the iPad's update button opens TestFlight");
      assert((await page.$$(".folderNewBtn")).length === 0, "no library behind it");
      await page.fill("input[autocomplete=username]", "sb-owner");
      await page.fill("input[autocomplete=current-password]", "sb-owner-pw");
      await page.click("form button[type=submit]");
      await page.locator(".setRow", { hasText: "sb-owner" }).waitFor();
      await page.click("button[aria-label='Export all']");
      const [download] = await Promise.all([
        page.waitForEvent("download", { timeout: 30000 }),
        page.locator(".ctxMenuItem", { hasText: "Everything (.zip)" }).click(),
      ]);
      assert(/^gamma-export-all-sb-owner-\d{4}-\d\d-\d\d\.zip$/.test(download.suggestedFilename()), download.suggestedFilename());
      const zip = fs.readFileSync(await download.path());
      assert(zip.subarray(0, 2).toString() === "PK" && zip.includes("sb-owner-"), "a zip holding each workspace's export");
      await page.locator(".setRow", { hasText: "Export of every personal workspace" }).waitFor();
      assertNoProblems(page, REFUSAL);
      await ctx.close();
    });
  } finally {
    await ipad.stop();
  }
}
