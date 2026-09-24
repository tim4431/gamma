// Publishing a page to Gamma Cloud (docs/dev/mirror.md "Publishing"). Three
// servers: the suite's own (the publishing server), a second Gamma started as
// the share host (cloud sign-in under provision with GAMMA_CLOUD_SHARE_HOST=1),
// and a stand-in account server both trust (../fakeCloud.mjs). The suite's
// server gets cloud sign-in from its admin; an account links its Gamma Cloud
// identity through the real round trip (the share popover's link action →
// Settings → Account → Link, the fake authorizing at once), then publishes a
// page from the share popover: the link on the share host opening for
// anyone, the synced state line, the cloud share's access, Sync now, the
// header pill and Settings' Publishing row, Unpublish, and Settings' Stop
// publishing all.
import { Account, Server } from "../harness.mjs";
import { FakeCloud } from "../fakeCloud.mjs";

export async function publishScenarios(env) {
  const { server, browser, step, openPage, assert, assertEq, assertNoProblems, until, flags } = env;
  if (flags.only && !"publish".includes(flags.only) && !flags.only.includes("publish")) return;
  const cloud = new FakeCloud();
  await cloud.start();
  const host = new Server({ env: { GAMMA_CLOUD_ISSUER: cloud.issuer, GAMMA_CLOUD_POLICY: "provision", GAMMA_CLOUD_SHARE_HOST: "1" } });
  let admin = null;
  try {
    await host.start();
    cloud.shareHost = host.base;
    server.manage("create-user", "pub-admin", "pub-admin-pw");
    server.manage("set-admin", "pub-admin", "on");
    admin = await new Account(server, "pub-admin", "pub-admin-pw").login();
    await admin.api("/api/admin/settings", { method: "PUT", body: { cloud_issuer: cloud.issuer } });
    server.manage("create-user", "pubber", "pubber-pw");
    const user = await new Account(server, "pubber", "pubber-pw").login();
    cloud.person("sub-pubber", "pubber");
    const paper = await user.api("/api/pages", { method: "POST", body: { title: "Published paper" } });
    await user.api(`/api/pages/${paper.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "pubblk1", parent: paper.id, position: "a0", content: "a note for the cloud" }] } });
    const pageUrl = `${server.base}/?page=${paper.id}&ws=${user.ws}`;
    const ctx = await user.context(browser);
    let page;
    const popover = () => page.locator(".sharePopover");
    const publishRow = () => popover().locator('.setRow[data-setting="Publish"]');
    async function openShare() {
      await page.getByRole("button", { name: "Share", exact: true }).click();
      await popover().waitFor();
    }

    await step("publish: the share popover asks for a Gamma Cloud account and the round trip links it", async () => {
      page = await openPage(ctx, pageUrl);
      await page.locator(".blockRow", { hasText: "a note for the cloud" }).waitFor({ timeout: 15000 });
      await openShare();
      await publishRow().getByText("Sign in with Gamma Cloud to publish.", { exact: true }).waitFor();
      await publishRow().getByRole("button", { name: "Link Gamma Cloud account", exact: true }).click();
      const settings = page.getByRole("dialog", { name: "Settings", exact: true });
      await settings.waitFor();
      await settings.getByRole("button", { name: "Link Gamma Cloud account", exact: true }).click();
      // through the account server and back to the page, now linked
      await page.waitForURL((u) => u.pathname === "/" && u.search.includes(paper.id) && !u.search.includes("cloud_error"), { timeout: 15000 });
      await page.locator(".blockRow", { hasText: "a note for the cloud" }).waitFor({ timeout: 15000 });
      const status = await user.api("/api/auth/cloud/status");
      assertEq(status.identity?.username, "pubber", "the account is linked to its cloud identity");
      // the Account pane's row says so, with the settings sync after it
      await page.getByRole("button", { name: "Account & settings", exact: true }).click();
      await page.getByRole("button", { name: "Settings…", exact: true }).click();
      await page.getByRole("navigation", { name: "Settings categories" }).getByRole("button", { name: "Account", exact: true }).click();
      const row = page.locator('.setRow[data-setting="Gamma Cloud"]');
      await row.locator(".uiTag", { hasText: "linked" }).waitFor();
      await until(() => row.locator(".settingDesc").textContent().then((t) => t.includes("Settings synced")),
        { timeout: 15000, what: "the settings sync hint on the link row" });
      const open = row.getByRole("link", { name: "Open account", exact: true });
      assertEq((await open.getAttribute("href") || "").replace(/\/$/, ""), cloud.issuer.replace(/\/$/, ""), "Open account goes to the portal");
      assertEq(await open.getAttribute("target"), "_blank", "the portal opens in a new tab");
      await page.keyboard.press("Escape");
      assertNoProblems(page);
    });

    let url = "";
    await step("publish: Publish puts the page on the share host, synced; the link opens there for anyone", async () => {
      await openShare();
      await publishRow().getByText("Keep this page reachable while this computer is off.", { exact: true }).waitFor({ timeout: 15000 });
      await publishRow().getByRole("button", { name: "Publish", exact: true }).click();
      const linkRow = popover().locator('.setRow[data-setting="Cloud link"]');
      await linkRow.waitFor({ timeout: 30000 });
      url = await linkRow.getByRole("button", { name: /Copy link|Copied/ }).getAttribute("title");
      assert(url.startsWith(`${host.base}/?share=`), `a share link on the share host (${url})`);
      const state = popover().locator(".publishState");
      await until(() => state.getAttribute("data-state").then((s) => s === "ok"), { timeout: 15000, what: "the state line reads synced" });
      assert((await state.textContent()).includes("Up to date"), "the pill's wording");
      // anyone opens it on the share host, which has no guest button to offer
      const anon = await browser.newContext({ viewport: { width: 1280, height: 860 } });
      try {
        const shared = await openPage(anon, url);
        await shared.waitForSelector(".readOnlyTitle", { timeout: 15000 });
        assert((await shared.textContent(".readOnlyTitle")).includes("Published paper"), "the title there");
        await shared.locator(".blockRow", { hasText: "a note for the cloud" }).waitFor();
        assertNoProblems(shared);
        const login = await openPage(anon, host.base);
        await login.getByRole("link", { name: "Sign in with Gamma Cloud", exact: true }).waitFor();
        assertEq(await login.locator(".loginGuestBtn").count(), 0, "a share host offers no guest");
        assertNoProblems(login);
      } finally { await anon.close(); }
      assertNoProblems(page);
      return url.replace(/share=(.{8}).*/, "share=$1…");
    });

    await step("publish: the cloud share's access, Sync now, the header pill and Settings' Publishing row", async () => {
      const token = new URL(url).searchParams.get("share");
      await popover().getByRole("button", { name: /Signed in/ }).click();
      await until(() => user.api(`/api/pages/${paper.id}/publish`).then((s) => s.share?.audience === "users"),
        { timeout: 20000, what: "the audience changed on the share host" });
      assertEq((await fetch(`${host.base}/api/share/${token}`)).status, 401, "signed-in only: an anonymous visitor is asked to sign in");
      const state = popover().locator(".publishState");
      await until(() => state.getAttribute("data-state").then((s) => s === "ok"), { timeout: 15000, what: "synced after the change" });
      await popover().getByRole("button", { name: "Sync now", exact: true }).click();
      await until(() => state.getAttribute("data-state").then((s) => s === "ok"), { timeout: 20000, what: "synced after Sync now" });
      await page.keyboard.press("Escape");
      await popover().waitFor({ state: "detached" });
      // the header's sync pill follows the publication and names it
      const pill = page.locator(".mirrorPill");
      await pill.waitFor();
      assert((await pill.getAttribute("title")).startsWith("Published to Gamma Cloud"), "the pill names the publication");
      await pill.click();
      await page.locator(".mirrorPopover .popoverTitle", { hasText: "Published to Gamma Cloud" }).waitFor();
      await page.locator(".mirrorPopover").getByRole("button", { name: "Sync settings", exact: true }).click();
      await page.locator(".mirrorPopover").getByText("Automatic sync", { exact: true }).waitFor();
      for (const hidden of ["Direction", "Force pull", "Force push", "Detach", "Remove origin"]) {
        assertEq(await page.locator(".mirrorPopover").getByText(hidden, { exact: false }).count(), 0, `no ${hidden} for a publication`);
      }
      await pill.click();
      // Settings → Workspaces: under Publishing, not among the clones
      await page.getByRole("button", { name: "Account & settings", exact: true }).click();
      await page.getByRole("button", { name: "Settings…", exact: true }).click();
      await page.getByRole("navigation", { name: "Settings categories" }).getByRole("button", { name: "Workspaces", exact: true }).click();
      const row = page.locator(".aiProvRow[data-publication]");
      await row.waitFor();
      assert((await row.textContent()).includes("1 published page"), "the count of published pages");
      await page.getByText("No clones yet.", { exact: true }).waitFor();
      await page.keyboard.press("Escape");
      assertNoProblems(page);
    });

    await step("publish: Unpublish takes the page off the share host and keeps it here", async () => {
      const token = new URL(url).searchParams.get("share");
      await openShare();
      await popover().locator('.setRow[data-setting="Cloud link"]').waitFor({ timeout: 15000 });
      await popover().getByRole("button", { name: "Unpublish", exact: true }).click();
      await popover().locator(".mirrorConfirm").getByRole("button", { name: "Unpublish", exact: true }).click();
      await publishRow().getByRole("button", { name: "Publish", exact: true }).waitFor({ timeout: 20000 });
      assertEq((await fetch(`${host.base}/api/share/${token}`)).status, 404, "the cloud link no longer opens");
      assertEq((await user.api(`/api/pages/${paper.id}/publish`)).published, false, "not published");
      assert((await page.locator(".blockRow", { hasText: "a note for the cloud" }).count()) === 1, "the page stays here");
      assertNoProblems(page);
    });

    await step("publish: Settings' Publishing row stops publishing every page", async () => {
      const again = await user.api(`/api/pages/${paper.id}/publish`, { method: "POST", body: {} });
      const token = new URL(again.url).searchParams.get("share");
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "Account & settings", exact: true }).click();
      await page.getByRole("button", { name: "Settings…", exact: true }).click();
      await page.getByRole("navigation", { name: "Settings categories" }).getByRole("button", { name: "Workspaces", exact: true }).click();
      const row = page.locator(".aiProvRow[data-publication]");
      await until(() => row.textContent().then((t) => t.includes("1 published page")), { what: "the row counts the page again" });
      await row.getByRole("button", { name: "More" }).click();
      await page.locator(".ctxMenu button", { hasText: "Stop publishing all" }).click();
      await page.getByRole("button", { name: "Stop publishing", exact: true }).click();
      await until(() => row.textContent().then((t) => t.includes("0 published pages")), { timeout: 20000, what: "nothing published" });
      assertEq((await fetch(`${host.base}/api/share/${token}`)).status, 404, "the cloud link no longer opens");
      await page.keyboard.press("Escape");
      assertNoProblems(page);
    });
    await ctx.close();
  } finally {
    if (admin) await admin.api("/api/admin/settings", { method: "PUT", body: { cloud_issuer: "" } }).catch(() => {});
    await host.stop();
    await cloud.stop();
  }
}
