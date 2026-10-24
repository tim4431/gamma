// Sign in with Gamma Cloud (docs/dev/cloud_accounts.md): while it is off the
// account pane says what it gives (with Set up for an admin), the admin turns
// it on under Settings → Server → Sign-in, the login page grows the button,
// and an account sees its link row. The round trip through a real account server
// is covered by backend/tests/test_cloud_auth.py; here the browser is never
// sent there (the button's target is asserted, not followed), except on the
// Gamma servers a step starts against the fake account server (fakeCloud.mjs).
import { Account, Server, wanted } from "../harness.mjs";
import { FakeCloud } from "../fakeCloud.mjs";

export async function cloudSignInScenarios(env) {
  const { server, browser, step, openPage, assert, assertEq, assertNoProblems, until, flags } = env;
  if (!wanted("cloud sign-in")) return;
  await server.manage("create-user", "cloud-admin", "cloud-admin-pw");
  await server.manage("set-admin", "cloud-admin", "on");
  const admin = await new Account(server, "cloud-admin", "cloud-admin-pw").login();
  const settingsNav = (page, name) => page.getByRole("navigation", { name: "Settings categories" }).getByRole("button", { name, exact: true });
  async function openSettings(page, pane) {
    await page.getByRole("button", { name: "Account & settings", exact: true }).click();
    await page.getByRole("menuitem", { name: "Settings…", exact: true }).click();
    await settingsNav(page, pane).click();
  }

  const cloudRow = (page) => page.locator('.setRow[data-setting="Gamma Cloud"]');
  const BENEFITS = "Publish pages for free and carry your settings to your other servers";

  // Off, the account pane still says what Gamma Cloud gives: a member sees it
  // marked off, an admin gets Set up, which lands on the switch.
  await step("cloud sign-in: while it is off the account pane offers it", async () => {
    await server.manage("create-user", "cloud-member", "cloud-member-pw");
    const member = await new Account(server, "cloud-member", "cloud-member-pw").login();
    const memberCtx = await member.context(browser);
    const ctx = await admin.context(browser);
    try {
      const memberPage = await openPage(memberCtx, server.base);
      await openSettings(memberPage, "Account & sync");
      await cloudRow(memberPage).getByText(BENEFITS, { exact: true }).waitFor();
      await cloudRow(memberPage).locator(".uiTag").filter({ hasText: /^off$/ }).waitFor();
      assertEq(await cloudRow(memberPage).getByRole("button").count(), 0, "nothing a member can press");
      await assertNoProblems(memberPage);
      const page = await openPage(ctx, server.base);
      await openSettings(page, "Account & sync");
      await cloudRow(page).getByText(BENEFITS, { exact: true }).waitFor();
      await cloudRow(page).getByRole("button", { name: "Set up", exact: true }).click();
      await page.getByRole("textbox", { name: "Account server", exact: true }).waitFor();
      await until(() => page.evaluate(() => document.activeElement?.dataset?.setting === "Account server"),
        { what: "Set up lands on the Account server row" });
      await assertNoProblems(page);
    } finally {
      await memberCtx.close();
      await ctx.close();
    }
  });

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
      // the account pane offers the link, saying what it gives
      await settingsNav(page, "Account & sync").click();
      await cloudRow(page).getByRole("button", { name: "Link Gamma Cloud account", exact: true }).waitFor();
      await cloudRow(page).getByText(BENEFITS, { exact: true }).waitFor();
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
      // Set up takes the admin to the Connect button
      await cloudRow(page).getByRole("button", { name: "Set up", exact: true }).click();
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
      await hosted.manage("create-user", "host-admin", "host-admin-pw");
      await hosted.manage("set-admin", "host-admin", "on");
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
      // a hosted container takes no guests, whatever GAMMA_GUEST_MAX says: no Guests section, no guest login
      await until(() => page.locator('.setSection[data-setting="Guests"]').count().then((n) => n === 0),
        { what: "the Guests section left out on a hosted container" });
      assertEq((await (await fetch(`${hosted.base}/api/server-config`)).json()).guest, false, "guest logins are off");
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
        // a Gamma Cloud server's login page is that one button; the password form folds behind Admin sign-in
        await login.locator("a.loginCloudBtn.lead").waitFor();
        assertEq(await login.getByPlaceholder("Username").count(), 0, "the password form is folded away");
        await login.getByRole("button", { name: "Admin sign-in", exact: true }).click();
        await login.getByPlaceholder("Username").waitFor();
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

  // A page shared by Gamma Cloud username, and requests to join
  // (docs/dev/cloud_accounts.md "Requests to join"): a second Gamma that
  // signs people in through the fake account server and admits invited
  // people only. Its owner links their own Gamma Cloud account (the lookup
  // runs on it) and names a newcomer in the Share popover's Gamma Cloud box;
  // the newcomer signs in from the link's gate and lands on the page.
  // Someone nobody invited asks to join instead: the gate says so, the
  // request heads the popover's people with Approve, and approved they get
  // in at their next sign-in. A sign-in from the login page asks the admin,
  // whose Server pane lists it.
  await step("cloud sign-in: a page shared by Gamma Cloud username, and requests to join", async () => {
    const cloud = new FakeCloud();
    await cloud.start();
    const lab = new Server({ env: { GAMMA_CLOUD_ISSUER: cloud.issuer, GAMMA_CLOUD_POLICY: "invited" } });
    const contexts = [];
    const context = async (account) => {
      const ctx = account ? await account.context(browser) : await browser.newContext({ viewport: { width: 1280, height: 860 } });
      contexts.push(ctx);
      return ctx;
    };
    const gate = [/GET \/api\/share\/[^ ]+ -> 401/];
    try {
      await lab.start();
      await lab.manage("create-user", "lab-owner", "lab-owner-pw");
      await lab.manage("set-admin", "lab-owner", "on");
      const owner = await new Account(lab, "lab-owner", "lab-owner-pw").login();
      const pg = await owner.api("/api/pages", { method: "POST", body: { title: "Cloud shared paper" } });
      const { token } = await owner.api(`/api/share/${pg.id}`, { method: "POST", body: { audience: "list", role: "view" } });
      const link = `${lab.base}/?share=${token}`;

      // the owner links their Gamma Cloud account on the way to the page, and invites by cloud username
      cloud.person("sub-lab-owner", "lab-owner");
      const page = await openPage(await context(owner),
        `${lab.base}/api/auth/cloud/start?link=1&next=${encodeURIComponent(`/?page=${pg.id}&ws=${owner.ws}`)}`);
      await page.click("button[aria-label='Share']");
      const pop = page.locator(".sharePopover");
      const people = pop.locator(".setSectionGroup", { hasText: "Who has access" });
      await people.getByRole("button", { name: "Gamma Cloud", exact: true }).click();
      cloud.person("sub-ann", "ann-cloud");
      await pop.getByRole("textbox", { name: "Gamma Cloud username" }).fill("@Ann-Cloud");
      await pop.getByRole("button", { name: "Invite", exact: true }).click();
      const annRow = pop.locator(".aiProvRow", { hasText: "ann-cloud" });
      await annRow.getByText("pending", { exact: true }).waitFor();
      await annRow.getByRole("button", { name: "Edit", exact: true }).click();
      await until(() => owner.api(`/api/share-settings/${pg.id}`).then((s) => s.pending[0]?.role === "edit"),
        { what: "the waiting invitation's role" });
      await assertNoProblems(page);

      // the newcomer: the link's gate offers Gamma Cloud, and the sign-in lands on the page
      const ann = await openPage(await context(), link);
      await ann.getByText("Sign in to open this shared page", { exact: true }).waitFor({ timeout: 15000 });
      await ann.getByText("asks the person who shared this page to let you in", { exact: false }).waitFor();
      await ann.getByRole("link", { name: "Sign in with Gamma Cloud", exact: true }).click();
      await until(async () => (await ann.textContent(".readOnlyTitle").catch(() => "")).includes("Cloud shared paper"),
        { what: "the page after the cloud sign-in" });
      assertEq((await owner.api(`/api/share-settings/${pg.id}`)).users[0]?.name, "ann-cloud", "the invitation was claimed");
      await assertNoProblems(ann, gate);

      // someone nobody invited asks to join: the gate says so
      cloud.person("sub-bea", "bea-cloud");
      const bea = await openPage(await context(), link);
      await bea.getByRole("link", { name: "Sign in with Gamma Cloud", exact: true }).click();
      await bea.getByRole("status").filter({ hasText: "Asked to join. Sign in again once your request is approved." }).waitFor();
      assert(!bea.url().includes("cloud_join"), "the outcome is dropped from the address bar");
      assert(bea.url().includes(`share=${token}`), "the gate keeps its link");
      assert((await owner.api("/api/inbox?filter=unread")).items.some((i) => i.kind === "join_request" && i.snippet === "bea-cloud"),
        "the owner hears of it");
      // the owner approves it in the popover: a waiting invitation to view
      await page.keyboard.press("Escape");
      await pop.waitFor({ state: "detached" });
      await page.click("button[aria-label='Share']");
      const beaRow = pop.locator(".aiProvRow", { hasText: "bea-cloud" });
      await beaRow.getByText("asks to join", { exact: true }).waitFor();
      await beaRow.getByRole("button", { name: "Approve", exact: true }).click();
      await beaRow.getByText("pending", { exact: true }).waitFor();
      assert(!(await owner.api("/api/inbox?filter=unread")).items.some((i) => i.kind === "join_request"), "the request's item settled");
      await assertNoProblems(page);
      // and her next sign-in lands on the page
      await bea.getByRole("link", { name: "Sign in with Gamma Cloud", exact: true }).click();
      await until(async () => (await bea.textContent(".readOnlyTitle").catch(() => "")).includes("Cloud shared paper"),
        { what: "the page after the approval" });
      await assertNoProblems(bea, gate);

      // from the login page the request goes to the admin, whose Server pane lists it
      cloud.person("sub-cyd", "cyd-cloud");
      const cyd = await openPage(await context(), lab.base);
      await cyd.getByText("asks this server's admin to let you in", { exact: false }).waitFor({ timeout: 15000 });
      await cyd.getByRole("link", { name: "Sign in with Gamma Cloud", exact: true }).click();
      await cyd.getByRole("status").filter({ hasText: "Asked to join." }).waitFor();
      await page.keyboard.press("Escape");
      await pop.waitFor({ state: "detached" });
      await openSettings(page, "Server");
      const cydRow = page.locator('.aiProvRow[data-join-request="cyd-cloud"]');
      await cydRow.getByText("from the login page", { exact: false }).waitFor();
      await cydRow.getByRole("button", { name: "Approve", exact: true }).click();
      await cydRow.locator(".uiTag").filter({ hasText: /^approved$/ }).waitFor();
      await assertNoProblems(page);
      await cyd.getByRole("link", { name: "Sign in with Gamma Cloud", exact: true }).click();
      await cyd.waitForSelector(".folderNewBtn", { timeout: 15000 });
      await assertNoProblems(cyd);
    } finally {
      for (const ctx of contexts) await ctx.close();
      await lab.stop();
      await cloud.stop();
    }
  });

  // A linked account's profile syncs through Gamma Cloud, and reading it may
  // first sync it, a round trip to the account server
  // (docs/dev/settings.md): the first load's combined preferences read
  // leaves it out and the app reads it on its own, so the open tabs, the
  // recents and the reading positions never wait on Gamma Cloud.
  await step("cloud sign-in: a linked account's first load reads its other preferences in one answer without the profile, and the profile on its own", async () => {
    const cloud = new FakeCloud();
    await cloud.start();
    const lab = new Server({ env: { GAMMA_CLOUD_ISSUER: cloud.issuer, GAMMA_CLOUD_POLICY: "invited" } });
    let ctx = null;
    try {
      await lab.start();
      await lab.manage("create-user", "sync-owner", "sync-owner-pw");
      const owner = await new Account(lab, "sync-owner", "sync-owner-pw").login();
      cloud.person("sub-sync-owner", "sync-owner");
      ctx = await owner.context(browser);
      const page = await openPage(ctx);
      const asked = [];
      page.on("request", (r) => {
        const url = new URL(r.url());
        if (url.pathname.startsWith("/api/prefs") && r.method() === "GET") asked.push(url.pathname + url.search);
      });
      for (const load of ["the load the link lands on", "a reload"]) {
        asked.length = 0;
        const combined = page.waitForResponse((r) => r.url().includes("/api/prefs?keys="));
        const alone = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/prefs/profile" && r.request().method() === "GET");
        if (load === "a reload") await page.reload();
        else await page.goto(`${lab.base}/api/auth/cloud/start?link=1&next=${encodeURIComponent(`/?ws=${owner.ws}`)}`);
        await page.locator(".folderNewBtn").first().waitFor({ timeout: 15000 });
        const answer = await (await combined).json();
        assertEq(Object.keys(answer.prefs).join(" "), "open-tabs recent-views read-pos ai-provider", `${load}: the combined read answers all but the profile`);
        assert((await (await alone).json()).key === "profile", `${load}: the profile is read on its own`);
        assertEq(asked.join(" "), "/api/prefs?keys=profile,open-tabs,recent-views,read-pos,ai-provider /api/prefs/profile",
          `${load}: one combined read, then the profile once`);
      }
      assertEq((await owner.api("/api/auth/cloud/status")).identity?.subject, "sub-sync-owner", "the account is linked");
      await assertNoProblems(page);
    } finally {
      if (ctx) await ctx.close();
      await lab.stop();
      await cloud.stop();
    }
  });
}
