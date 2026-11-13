import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Account, PYTHON, ROOT, Server, wanted } from "../harness.mjs";

// A computer's channel and its presence (docs/dev/computers.md): two
// Gammas, a NAS (a plain server, no agents, its grace before a computer
// reads offline cut to 3 s) and a computer (the desktop app's own server: the
// vaults recipe of GAMMA_FOLDER_LINKS with no shell secret, local agents on,
// the stand-in Codex found and Claude Code not). On the NAS a shared
// workspace's folder holds a page; on the computer its maker's own account
// makes a vault of that folder with a read token minted on the NAS, as the
// shell does, then hands the computer a `computer` token minted there. The
// maker's plain browser on the NAS then shows the page's computer chip: the
// computer's host name, Connected, the folder, the assistants there; another
// member of the workspace and a share link's reader see none; the computer
// stopped, the maker's chip turns Offline since … by itself.
const FAKES = path.join(ROOT, "backend", "tests", "fake_agents");
const GRACE_S = 3;

export async function computerScenarios(env) {
  const { browser, step, openPage, assert, assertEq, assertNoProblems, until } = env;
  if (!wanted("computers")) return;
  const disk = fs.mkdtempSync(path.join(os.tmpdir(), "gamma-e2e-computers-"));
  const nas = new Server({ env: { GAMMA_COMPUTER_GRACE_S: String(GRACE_S) } });
  const computer = new Server({ env: {
    GAMMA_FOLDER_LINKS: "1", GAMMA_SHELL_SECRET: "", GAMMA_LOCAL_AGENTS: "1",
    GAMMA_AGENT_CODEX: JSON.stringify([PYTHON, path.join(FAKES, "fake_codex.py")]),
    // no Claude Code here: a path that is no file, so the computer finds none
    GAMMA_AGENT_CLAUDE: path.join(disk, "no-claude.exe"),
  } });
  let computerUp = false;
  try {
    await nas.start();
    await computer.start();
    computerUp = true;
    for (const [name, pw] of [["pc-alice", "pc-alice-pw"], ["pc-bob", "pc-bob-pw"]]) await nas.manage("create-user", name, pw);
    const out = await nas.manage("create-workspace", "Cavity lab", "pc-alice", "shared");
    const wsId = (out.match(/workspace (\S+)/) || [])[1];
    assert(wsId, `shared workspace id from: ${out}`);
    await nas.manage("set-member", wsId, "pc-bob", "editor");
    const alice = await new Account(nas, "pc-alice", "pc-alice-pw").login();
    const bob = await new Account(nas, "pc-bob", "pc-bob-pw").login();
    alice.ws = bob.ws = wsId;
    // the computer's own account, which the desktop window is signed into
    await computer.manage("create-user", "pc-owner", "pc-owner-pw");
    const owner = await new Account(computer, "pc-owner", "pc-owner-pw").login();

    const pg = await alice.api("/api/pages", { method: "POST", body: { title: "Resonator notes" } });
    await alice.file(pg.id, { folders: ["Cavity"] });
    const folderId = await alice.folder("Cavity");
    const at = `${nas.base}/?ws=${wsId}&page=${pg.id}`;
    const computersOf = async (account) => (await account.api(`/api/pages/${pg.id}/computer`)).computers;
    const chip = (page) => page.locator(".vaultComputerChip");
    // The page loaded and its computers read (usePageComputer's GET), with that answer.
    const openAndRead = async (ctx, url) => {
      const page = await openPage(ctx);
      const read = page.waitForResponse((r) => r.url().includes(`/api/pages/${pg.id}/computer`), { timeout: 15000 });
      await page.goto(url);
      await page.locator(".pageTitleRow", { hasText: "Resonator notes" }).waitFor();
      return { page, answer: await (await read).json() };
    };

    let name = "";
    await step("computers: a vault of a NAS folder made on the computer with a NAS token, and the computer shown there with a computer token", async () => {
      // the agents as the computer finds them: Codex (the stand-in), no Claude Code
      const found = (await owner.api("/api/local-agents?fresh=1")).agents;
      const codex = found.find((a) => a.name === "codex"), claude = found.find((a) => a.name === "claude");
      assert(codex?.found && codex.ready, `Codex is found and ready: ${JSON.stringify(codex)}`);
      assert(claude && !claude.found, `Claude Code is not on this computer: ${JSON.stringify(claude)}`);
      // as the shell's addFolderVault: a read token on the NAS (the vault does not push), the vault on the host
      const read = await alice.api("/api/integrations/tokens", { method: "POST",
        body: { name: "Gamma desktop: Cavity synced vault", scope: "read", expires_in_days: 365 } });
      const dir = path.join(disk, "Cavity");
      fs.mkdirSync(dir);
      const vault = await owner.api("/api/folder-links", { method: "POST", body: { folder: folderId, path: dir, notes: true, pull: true,
        push: "off", remote_url: nas.base, workspace: wsId, token: read.token, token_id: read.id } });
      assertEq(vault.remote_url, nas.base, "the vault reads the NAS");
      // as the shell's showComputer: a computer token minted on the NAS, handed to the host
      const minted = await alice.api("/api/integrations/tokens", { method: "POST",
        body: { name: `Gamma desktop: ${os.hostname()}`, scope: "computer", expires_in_days: 365 } });
      const made = await owner.api("/api/computer-links", { method: "POST",
        body: { remote_url: nas.base, workspace: wsId, token: minted.token, token_id: minted.id } });
      assertEq(made.replaced, null, "no link replaced");
      assertEq(JSON.stringify([made.link.workspace_id, made.link.username]), JSON.stringify([wsId, "pc-alice"]),
        "the link names the workspace and the NAS account the announce answered");
      // the channel connects (its first poll answered: a hold of up to 25 s), and the NAS knows the computer through it
      await until(async () => (await owner.api("/api/computer-links")).links.find((l) => l.id === made.link.id)?.status?.state === "connected",
        { timeout: 45000, every: 500, what: "the channel connected" });
      const [seen] = await until(async () => {
        const list = await computersOf(alice);
        return list.length && list[0].online && list[0].assistants.some((a) => a.name === "codex" && a.found && a.ready) ? list : null;
      }, { timeout: 20000, what: "the page's computer, online with Codex" });
      name = seen.name;
      assertEq(name.toLowerCase(), os.hostname().slice(0, 64).toLowerCase(), "the computer's host name");
      assertEq(JSON.stringify(seen.vault), JSON.stringify({ kind: "folder", name: "Cavity", paused: false }), "the folder it keeps, named by the NAS");
      assertEq(seen.since, null, "online: no offline time");
      assert(!JSON.stringify(seen).includes(disk), "no path of the computer reaches the NAS");
    });

    await step("computers: the maker's plain browser on the NAS shows the page's computer chip: its name, Connected, the folder and Codex", async () => {
      const ctx = await alice.context(browser);
      try {
        const { page, answer } = await openAndRead(ctx, at);
        assertEq(answer.computers.length, 1, "the page's read names the computer");
        const button = chip(page);
        await button.waitFor({ timeout: 10000 });
        assertEq(await button.getAttribute("data-computer"), "online", "the chip's dot says online");
        assertEq(await button.getAttribute("aria-label"), `Synced vault on ${name}: Connected`, "the chip's name");
        await button.click();
        const card = page.locator(".vaultPopover");
        await card.waitFor();
        assertEq((await card.locator(".popoverTitle").innerText()).trim(), `Synced vault on ${name}`, "the card's title");
        assertEq((await card.locator(".vaultComputerState").innerText()).trim(), "Connected", "its state");
        assertEq(await card.locator(".vaultComputerState").getAttribute("data-computer"), "online");
        const lines = (await card.locator(".popoverHint").allInnerTexts()).map((s) => s.trim());
        assertEq(JSON.stringify(lines), JSON.stringify(["Connected", "Folder: Cavity", "Assistants there: Codex"]),
          "the state, the folder and the assistants there (no Claude Code, not found)");
        await page.keyboard.press("Escape");
        await card.waitFor({ state: "detached" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("computers: another member of the workspace and a share link's reader see no computer on the page", async () => {
      assertEq(JSON.stringify(await computersOf(bob)), "[]", "the page's computers for another member");
      const ctx = await bob.context(browser);
      try {
        const { page, answer } = await openAndRead(ctx, at);
        assertEq(JSON.stringify(answer), JSON.stringify({ computers: [] }), "the page's read answers him none");
        await page.waitForTimeout(500);
        assertEq(await chip(page).count(), 0, "no computer chip for him");
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const { token } = await alice.api(`/api/share/${pg.id}`, { method: "POST" });
      assertEq((await fetch(`${nas.base}/api/pages/${pg.id}/computer?share=${token}`)).status, 404, "a share asks for none");
      const reader = await browser.newContext({ viewport: { width: 1280, height: 860 } });
      try {
        const page = await openPage(reader);
        const asked = [];
        page.on("request", (r) => { if (r.url().includes("/computer")) asked.push(r.url()); });
        await page.goto(`${nas.base}/?share=${token}`);
        await page.waitForSelector(".readOnlyTitle");
        await page.waitForTimeout(500);
        assertEq(await chip(page).count(), 0, "no computer chip for a share's reader");
        assertEq(JSON.stringify(asked), "[]", "the share's page never asks for the page's computers");
        assertNoProblems(page);
      } finally { await reader.close(); }
    });

    await step("computers: the computer stopped, the maker's open chip turns Offline since … by itself within the grace", async () => {
      const ctx = await alice.context(browser);
      try {
        const { page } = await openAndRead(ctx, at);
        const button = chip(page);
        await button.waitFor({ timeout: 10000 });
        assertEq(await button.getAttribute("data-computer"), "online", "online before");
        await computer.stop();
        computerUp = false;
        // its held poll ends (at most the 25 s hold), then the grace: the NAS's
        // {t: "computer"} nudge, or the poll fallback's next read, turns the chip
        await until(async () => (await button.getAttribute("data-computer").catch(() => "")) === "offline",
          { timeout: 25000 + GRACE_S * 1000 + 15000, every: 250, what: "the chip turns offline" });
        const label = await button.getAttribute("aria-label");
        assert(new RegExp(`^Synced vault on ${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}: Offline since \\S`).test(label),
          `the chip reads offline since a time: ${label}`);
        await button.click();
        const card = page.locator(".vaultPopover");
        await card.waitFor();
        const state = (await card.locator(".vaultComputerState").innerText()).trim();
        assert(/^Offline since \d{1,2}:\d{2}\s?[AP]M$/.test(state), `the card says since when, today's time: ${state}`);
        assertEq(await card.locator(".vaultComputerState").getAttribute("data-computer"), "offline");
        await page.keyboard.press("Escape");
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const [gone] = await computersOf(alice);
      assertEq(gone?.online, false, "the NAS reads it offline");
      assert(gone.since && !Number.isNaN(Date.parse(gone.since)), `with the time it went offline: ${gone.since}`);
    });
  } finally {
    if (computerUp) await computer.stop();
    await nas.stop();
    fs.rmSync(disk, { recursive: true, force: true });
  }
}
