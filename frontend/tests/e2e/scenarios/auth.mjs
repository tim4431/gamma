// Signing in: an unavailable explicit workspace, the login page, guest login
// (a fresh throwaway account each time, docs/dev/guests.md), demo mode, and
// what a new account's library offers ("Start your library").
import { Account } from "../harness.mjs";

export async function authScenarios({ server, browser, alice, step, until, assert, assertEq, assertNoProblems, openPage }) {
  await step("workspaces: an unavailable explicit workspace never falls back to another library", async () => {
    const ctx = await alice.context(browser);
    const page = await openPage(ctx, `${server.base}/?ws=unavailable-workspace`);
    await page.getByText("This workspace is unavailable", { exact: true }).waitFor();
    assert(new URL(page.url()).searchParams.get("ws") === "unavailable-workspace", "the requested workspace stays in the URL");
    assert((await page.$$(".folderNewBtn")).length === 0, "no fallback library is opened");
    assertNoProblems(page);
    await page.getByRole("button", { name: "Open my workspaces" }).click();
    await page.waitForSelector(".folderNewBtn", { timeout: 15000 });
    assertNoProblems(page);
    await ctx.close();
  });

  await step("auth: login page, wrong password, then sign in", async () => {
    const ctx = await browser.newContext();
    const page = await openPage(ctx, `${server.base}/`);
    await page.waitForSelector(".loginInput");
    assert((await page.textContent(".loginFoot")).includes("Accounts are made by the person who runs this server."), "who makes accounts");
    await page.fill(".loginInput >> nth=0", "alice");
    await page.fill("input[type=password]", "nope");
    await page.click(".loginBtn");
    await page.waitForSelector(".loginError");
    assert((await page.textContent(".loginError")).includes("That username and password don't match."), "names the mismatch");
    assert(await page.evaluate(() => document.activeElement?.type === "password" && document.activeElement.getAttribute("aria-invalid") === "true"),
      "the password field takes the focus, marked invalid");
    // a locked-out sign-in and an unreachable server say so, not "wrong password"
    await page.route("**/api/login", (route) => route.fulfill({ status: 429, contentType: "application/json", body: "{}" }));
    await page.click(".loginBtn");
    await until(async () => (await page.textContent(".loginError")).includes("Too many attempts."), { what: "the rate-limit message" });
    await page.unroute("**/api/login");
    await page.route("**/api/login", (route) => route.abort());
    await page.click(".loginBtn");
    await until(async () => (await page.textContent(".loginError")).includes("Can't reach the server."), { what: "the network message" });
    await page.unroute("**/api/login");
    await page.fill("input[type=password]", "alice-pw");
    await page.click(".loginBtn");
    await page.waitForSelector(".folderNewBtn", { timeout: 15000 });
    assertNoProblems(page, [/401/, /429/]);
    await ctx.close();
  });

  await step("auth: guest login lands on the welcome page, the card says when it goes, each guest is new", async () => {
    const ctx = await browser.newContext();
    try {
      const page = await openPage(ctx, `${server.base}/`);
      await page.waitForSelector(".loginGuestBtn");
      assertEq(await page.getByRole("button", { name: "Try the demo" }).count(), 0, "no demo button off a demo server");
      assert(/^A private workspace for \d+ hours?, then deleted$/.test(await page.textContent(".loginGuestNote")), "the guest button says the workspace goes");
      await page.click(".loginGuestBtn");
      await page.locator(".pageCard, .fileRow", { hasText: "Welcome" }).first().waitFor({ timeout: 15000 });
      const session = await page.evaluate(() => fetch("/api/session").then((r) => r.json()));
      assert(/^guest-/.test(session.user) && session.is_guest, `a guest-<random> account (${session.user})`);
      assert(session.guest_expires_at, "the session names when the guest goes");
      await page.getByRole("button", { name: "Account & settings", exact: true }).click();
      const role = page.locator(".userCardRole");
      await role.waitFor();
      const line = await role.textContent();
      assert(/^Temporary workspace · deleted in \d+ (hours?|minutes?)$/.test(line), `the card names the expiry (${line})`);
      // A second guest login is another account, with its own workspace.
      const other = await (await fetch(`${server.base}/api/login-guest`, { method: "POST" })).json();
      assert(/^guest-/.test(other.username) && other.username !== session.user, "every guest login mints a fresh account");
      // Logging out deletes a guest: the menu says so first.
      await page.getByRole("button", { name: "Log out", exact: true }).click();
      const confirm = page.locator(".confirmModal", { hasText: "Log out and delete this workspace?" });
      await confirm.waitFor();
      await confirm.getByRole("button", { name: "Log out and delete", exact: true }).click();
      await page.waitForSelector(".loginGuestBtn");
      const after = await page.evaluate(() => fetch("/api/session").then((r) => r.json()));
      assertEq(after.user, null, "signed out");
      assertNoProblems(page, [/401/]);
    } finally { await ctx.close(); }
  });

  await step("auth: demo mode is a small landing that leads with Try the demo, folds the sign-in and offers the first tour", async () => {
    server.manage("create-user", "auth-admin", "auth-admin-pw");
    server.manage("set-admin", "auth-admin", "on");
    const admin = await new Account(server, "auth-admin", "auth-admin-pw").login();
    await admin.api("/api/admin/settings", { method: "PUT", body: { demo_mode: true } });
    const ctx = await browser.newContext({ suggestTours: true });
    try {
      const { guest_ttl_hours: hours } = await admin.api("/api/admin/settings");
      const page = await openPage(ctx, `${server.base}/`);
      const demo = page.getByRole("button", { name: "Try the demo", exact: true });
      await demo.waitFor();
      await page.getByText(`Your own workspace for ${hours} hours, then it is deleted.`, { exact: true }).waitFor();
      // a small landing: the product pictured, what it does; no sample library promised without a seed
      await page.getByRole("heading", { name: "Read papers. Keep what you learn.", exact: true }).waitFor();
      await until(() => page.locator(".loginScene img").evaluate((img) => img.complete && img.naturalWidth > 0), { what: "the picture loads" });
      assertEq(await page.locator(".loginPoints li").count(), 2, "no sample-library promise without GAMMA_GUEST_SEED");
      assertEq(await page.locator(".loginInput").count(), 0, "the password form is folded");
      assertEq(await page.locator(".loginGuestBtn").count(), 0, "one guest button, the demo one");
      const disclosure = page.getByRole("button", { name: "Admin sign-in", exact: true });
      assertEq(await disclosure.getAttribute("aria-expanded"), "false");
      await disclosure.click();
      await page.locator(".loginInput").first().waitFor();
      await disclosure.click();
      await until(() => page.locator(".loginInput").count().then((n) => n === 0));
      await demo.click();
      await page.locator(".pageCard, .fileRow", { hasText: "Welcome" }).first().waitFor({ timeout: 15000 });
      // The first-run tour is offered on the library, and the guide keeps
      // its progress for this visit only.
      await page.locator('[data-guide-offer="first-run"] .guideCard').waitFor({ timeout: 15000 });
      const kept = await page.evaluate(() => ({
        session: sessionStorage.getItem("gamma-guide:first-run"), local: localStorage.getItem("gamma-guide:first-run"),
      }));
      assertEq(JSON.parse(kept.session)?.state, "offered");
      assertEq(kept.local, null, "nothing kept in localStorage");
      // The offer is the tour's welcome card; Start begins with the first
      // demo, not a second welcome.
      assertEq(await page.locator('[data-guide-offer="first-run"] .guideTitle').textContent(), "Welcome to the Gamma demo");
      await page.getByRole("button", { name: "Start the tour", exact: true }).click();
      await page.locator('[data-guide-overlay="add-demo"] .guideCard').waitFor();
      await page.keyboard.press("Escape");
      await page.locator('[data-guide-overlay]').waitFor({ state: "detached" });
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await admin.api("/api/admin/settings", { method: "PUT", body: { demo_mode: false } });
    }
  });

  await step("library: a new account's library offers Start your library above the Welcome page, gone with the first page", async () => {
    server.manage("create-user", "fresh-user", "fresh-user-pw");
    const fresh = await new Account(server, "fresh-user", "fresh-user-pw").login();
    const ctx = await fresh.context(browser);
    try {
      const page = await openPage(ctx, `${server.base}/?ws=${fresh.ws}`);
      const panel = page.locator('[data-guide="home.empty"]');
      await panel.waitFor({ timeout: 15000 });
      await page.locator(".fileRow", { hasText: "Welcome" }).waitFor(); // the seeded page stays listed below
      assertEq(await page.locator(".folderNewBtn").count(), 2, "New page and New folder stay");
      assertEq(await page.locator(".homeListBar ~ .empty").count(), 0, "the panel replaces the empty line");
      // A paper from a link: the Add popover with its URL field focused.
      await panel.getByRole("button", { name: /Open a paper from a link/ }).click();
      await page.waitForSelector('[data-guide="add.urlInput"]:focus');
      await page.locator('[data-guide="header.add"]').click();
      await until(() => page.locator('[data-guide="add.urlInput"]').count().then((n) => n === 0));
      // The Import dialog.
      await panel.getByRole("button", { name: /Import a library/ }).click();
      const dialog = page.getByRole("dialog", { name: "Import" });
      await dialog.waitFor();
      await page.keyboard.press("Escape");
      await dialog.waitFor({ state: "detached" });
      // The first tour.
      await panel.getByRole("button", { name: /Take the 2-minute tour/ }).click();
      await page.locator('[data-guide-overlay="welcome"] .guideCard').waitFor();
      await page.keyboard.press("Escape");
      await page.locator("[data-guide-overlay]").waitFor({ state: "detached" });
      // A note page of the user's own: the library is no longer new.
      await panel.getByRole("button", { name: /Write a note page/ }).click();
      await until(() => /[?&](block|page)=/.test(page.url()), { what: "the new page opens" });
      await page.locator('[data-guide="header.home"]').click();
      await page.locator(".fileRow", { hasText: "Welcome" }).waitFor();
      assertEq(await panel.count(), 0, "gone once the library holds a page of the user's own");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });
}
