// Sign in with Gamma Cloud (docs/dev/cloud_accounts.md): the admin turns it
// on under Settings → Server → Sign-in, the login page grows the button, and
// an account sees its link row. The round trip through a real account server
// is covered by backend/tests/test_cloud_auth.py; here the browser is never
// sent there (the button's target is asserted, not followed).
import { Account, Server, wanted } from "../harness.mjs";
import { FakeCloud } from "../fakeCloud.mjs";

export async function cloudSignInScenarios(env) {
  const { server, browser, step, openPage, assert, assertEq, assertNoProblems, until, flags } = env;
  if (!wanted("cloud sign-in")) return;
  server.manage("create-user", "cloud-admin", "cloud-admin-pw");
  server.manage("set-admin", "cloud-admin", "on");
  const admin = await new Account(server, "cloud-admin", "cloud-admin-pw").login();
  const settingsNav = (page, name) => page.getByRole("navigation", { name: "Settings categories" }).getByRole("button", { name, exact: true });
  async function openSettings(page, pane) {
    await page.getByRole("button", { name: "Account & settings", exact: true }).click();
    await page.getByRole("button", { name: "Settings…", exact: true }).click();
    await settingsNav(page, pane).click();
  }

  await step("cloud sign-in: the admin turns it on and the login page offers it", async () => {
    const ctx = await admin.context(browser);
    try {
      const page = await openPage(ctx, server.base);
      await openSettings(page, "Server");
      const issuer = page.getByRole("textbox", { name: "Account server", exact: true });
      await issuer.waitFor();
      assertEq((await admin.api("/api/admin/settings")).cloud.enabled, false, "off until an admin sets it");
      const chip = (text) => page.locator(".settingsPane .uiTag").filter({ hasText: new RegExp(`^${text}$`) }).first();
      await chip("off").waitFor();
      await issuer.fill("https://account.example");
      await page.getByRole("button", { name: "Claim", exact: true }).click();
      await page.getByRole("button", { name: "Save sign-in", exact: true }).click();
      await until(() => admin.api("/api/admin/settings").then((v) => v.cloud.enabled && v.cloud.policy === "claim"));
      await chip("on").waitFor();
      // the account pane offers the link
      await settingsNav(page, "Account & sync").click();
      await page.getByRole("button", { name: "Link Gamma Cloud account", exact: true }).waitFor();
      await assertNoProblems(page);
      // a signed-out visitor sees the button, aimed at this server's start endpoint
      const anon = await browser.newContext();
      try {
        const login = await openPage(anon, server.base);
        const button = login.getByRole("link", { name: "Sign in with Gamma Cloud", exact: true });
        await button.waitFor();
        const href = await button.getAttribute("href");
        assert(href.startsWith("/api/auth/cloud/start?next="), `the button starts the flow here (${href})`);
        // a refused sign-in comes back as a message on this page
        await login.goto(`${server.base}/?cloud_error=${encodeURIComponent("Sign-in cancelled.")}`);
        await login.getByRole("alert").filter({ hasText: "Sign-in cancelled." }).waitFor();
        assert(!login.url().includes("cloud_error"), "the message is dropped from the address bar");
        await assertNoProblems(login);
      } finally { await anon.close(); }
      // turning it off removes the button
      await admin.api("/api/admin/settings", { method: "PUT", body: { cloud_issuer: "" } });
      const anon2 = await browser.newContext();
      try {
        const login = await openPage(anon2, server.base);
        await login.getByPlaceholder("Username").waitFor();
        assertEq(await login.getByRole("link", { name: "Sign in with Gamma Cloud", exact: true }).count(), 0, "no button when off");
      } finally { await anon2.close(); }
    } finally {
      // off again even when a check above failed: the next step starts from off
      await admin.api("/api/admin/settings", { method: "PUT", body: { cloud_issuer: "" } });
      await ctx.close();
    }
  });

  // A server at a public address cannot sign in as the desktop client: until
  // it is connected there is no cloud button, the Link row waits, and the
  // Server pane offers Connect. The round trip itself leaves for the account
  // server (backend/tests/test_cloud_auth.py fakes it); here the account
  // server cannot be reached, so the browser comes straight back to the
  // Server pane with the error — the return path the success takes too.
  await step("cloud sign-in: a server at a public address is connected, not given the desktop client", async () => {
    await admin.api("/api/admin/settings", { method: "PUT", body: { cloud_issuer: "https://account.example", public_url: "https://gamma.example.org" } });
    const ctx = await admin.context(browser);
    try {
      assertEq((await admin.api("/api/admin/settings")).cloud.needs_connect, true, "the desktop client cannot serve a public address");
      const anon = await browser.newContext();
      try {
        const login = await openPage(anon, server.base);
        await login.getByPlaceholder("Username").waitFor();
        assertEq(await login.getByRole("link", { name: "Sign in with Gamma Cloud", exact: true }).count(), 0, "no button before it is connected");
      } finally { await anon.close(); }
      const page = await openPage(ctx, server.base);
      await openSettings(page, "Account & sync");
      await page.getByText("An admin connects this server to Gamma Cloud first", { exact: false }).waitFor();
      assertEq(await page.getByRole("button", { name: "Link Gamma Cloud account", exact: true }).count(), 0, "no Link button yet");
      await settingsNav(page, "Server").click();
      await page.getByRole("button", { name: "Connect", exact: true }).click();
      await page.waitForURL((url) => !url.pathname.startsWith("/api/"));
      await page.getByRole("alert").filter({ hasText: "account server" }).waitFor();
      assert(!page.url().includes("cloud_connect"), "the outcome is dropped from the address bar");
      await page.getByRole("button", { name: "Connect", exact: true }).waitFor();   // back on the Server pane
      await assertNoProblems(page);
    } finally {
      await admin.api("/api/admin/settings", { method: "PUT", body: { cloud_issuer: "", public_url: "" } });
      await ctx.close();
    }
  });

  // Invitations by cloud username (docs/dev/workspaces.md): the invite editor
  // offers the second way only while cloud sign-in is on. The lookup runs on
  // the inviter's own linked Gamma Cloud grant, and this admin has none, so
  // the invitation is refused with a message; the lookup through a real
  // grant, the pending row and its claim on first sign-in are
  // backend/tests/test_pending_memberships.py.
  await step("cloud sign-in: a workspace owner can name a Gamma Cloud username", async () => {
    const lab = await admin.api("/api/workspaces", { method: "POST", body: { name: "Cloud invite lab", kind: "shared" } });
    const ctx = await admin.context(browser);
    try {
      const page = await openPage(ctx, server.base);
      const openInvite = async () => {
        await openSettings(page, "Workspaces");
        await page.locator(".aiProvRow").filter({ hasText: "Cloud invite lab" }).getByRole("button", { name: "Manage" }).click();
        await page.getByRole("button", { name: "Invite", exact: true }).click();
        return page.getByRole("dialog", { name: "Invite to Cloud invite lab", exact: true });
      };
      // off: only accounts on this server
      let dialog = await openInvite();
      await dialog.waitFor();
      assertEq(await dialog.getByRole("button", { name: "Gamma Cloud username", exact: true }).count(), 0, "no cloud choice while sign-in is off");
      // on: the second way to name a person
      await admin.api("/api/admin/settings", { method: "PUT", body: { cloud_issuer: "https://account.example" } });
      await page.reload();
      dialog = await openInvite();
      await dialog.getByRole("button", { name: "Gamma Cloud username", exact: true }).click();
      const field = dialog.getByRole("textbox", { name: /Gamma Cloud username/ });
      await field.fill("@Alice");
      await dialog.getByRole("button", { name: "Invite", exact: true }).click();
      await dialog.locator(".aiKeysError").filter({ hasText: "Link your own Gamma Cloud account" }).waitFor();
      assertEq((await admin.api(`/api/workspaces/${lab.id}/invites`)).invites.length, 0, "nothing pending after a refused lookup");
      await assertNoProblems(page, [/POST \/api\/workspaces\/[^/]+\/invites -> 503/, /Link your own Gamma Cloud account/]);
    } finally {
      await admin.api("/api/admin/settings", { method: "PUT", body: { cloud_issuer: "" } });
      await ctx.close();
    }
  });

  // A hosted container (docs/dev/cloud_accounts.md "Hosted containers"): a
  // second Gamma started with GAMMA_HOSTED=1 learns its plan from the fake
  // account server at startup. The Server pane shows the plan and its caps
  // and the storage rows name the cap; a plan turned read-only arrives with
  // Sync now, the pane and the login page say so, and a write is refused.
  await step("cloud sign-in: a hosted container shows its plan and goes read-only", async () => {
    const cloud = new FakeCloud();
    await cloud.start();
    const active = { plan: "pro", status: "active", read_only: false, policy: "invited", max_accounts: 10,
      quota_mb: 1000, max_upload_mb: 100, offsite: { interval_s: 3600, keep: 7 }, grace_until: null, message: "" };
    cloud.hosted = { clientId: "gc_e2e", secret: "e2e-secret", reports: [], answer: active };
    const hosted = new Server({ env: { GAMMA_HOSTED: "1", GAMMA_CLOUD_ISSUER: cloud.issuer, GAMMA_CLOUD_CLIENT_ID: "gc_e2e",
      GAMMA_CLOUD_CLIENT_SECRET: "e2e-secret" } });
    let ctx = null;
    try {
      await hosted.start();
      hosted.manage("create-user", "host-admin", "host-admin-pw");
      hosted.manage("set-admin", "host-admin", "on");
      const owner = await new Account(hosted, "host-admin", "host-admin-pw").login();
      await until(() => owner.api("/api/admin/settings").then((v) => v.hosted?.limits?.plan === "pro"), { what: "the startup sync" });
      ctx = await owner.context(browser);
      const page = await openPage(ctx, hosted.base);
      await openSettings(page, "Server");
      const planRow = page.locator('.setRow[data-setting="Plan"]');
      await planRow.locator(".uiTag").filter({ hasText: /^active$/ }).waitFor();
      await planRow.getByText(/^pro · last synced/).waitFor();
      await page.locator('.setRow[data-setting="Plan limits"]').getByText(/\d+ of 10 accounts/).waitFor();
      await page.locator('.setRow[data-setting="Default quota"]').getByText("The plan's cap of 1000 MB", { exact: false }).waitFor();
      await page.locator('.setRow[data-setting="Unknown cloud accounts"]').getByText("Set by this server's plan", { exact: true }).waitFor();
      await assertNoProblems(page);
      // the plan lapses: Sync now brings it, and the pane says what still works
      cloud.hosted.answer = { ...active, status: "read_only", read_only: true };
      await planRow.getByRole("button", { name: "Sync now", exact: true }).click();
      await planRow.locator(".uiTag").filter({ hasText: /^read-only$/ }).waitFor();
      await page.getByRole("alert").filter({ hasText: "This server is read-only" }).first().waitFor();
      const refused = await owner.api("/api/pages", { method: "POST", body: { title: "Too late" } }).catch((e) => e);
      assertEq(refused.status, 423, "a write is refused while read-only");
      // a signed-out visitor sees it on the login page, without the guest button
      const anon = await browser.newContext();
      try {
        const login = await openPage(anon, hosted.base);
        await login.getByRole("alert").filter({ hasText: "This server is read-only" }).waitFor();
        assertEq(await login.getByRole("button", { name: "Continue as guest", exact: true }).count(), 0, "no guest while read-only");
        await assertNoProblems(login);
      } finally { await anon.close(); }
      // and paid up again it takes writes
      cloud.hosted.answer = active;
      await planRow.getByRole("button", { name: "Sync now", exact: true }).click();
      await planRow.locator(".uiTag").filter({ hasText: /^active$/ }).waitFor();
      await owner.api("/api/pages", { method: "POST", body: { title: "Back again" } });
      // what the app wrote on its own while read-only (its preference sync) was refused, as it should be
      await assertNoProblems(page, [/-> 423$/]);
    } finally {
      if (ctx) await ctx.close();
      await hosted.stop();
      await cloud.stop();
    }
  });
}
