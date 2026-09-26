// Signing in: an unavailable explicit workspace, the login page, guest login
// (a fresh throwaway account each time, docs/dev/guests.md) and demo mode.
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
    await page.fill(".loginInput >> nth=0", "alice");
    await page.fill("input[type=password]", "nope");
    await page.click(".loginBtn");
    await page.waitForSelector(".loginError");
    await page.fill("input[type=password]", "alice-pw");
    await page.click(".loginBtn");
    await page.waitForSelector(".folderNewBtn", { timeout: 15000 });
    assertNoProblems(page, [/401/]);
    await ctx.close();
  });

  await step("auth: guest login lands on the welcome page, the card says when it goes, each guest is new", async () => {
    const ctx = await browser.newContext();
    try {
      const page = await openPage(ctx, `${server.base}/`);
      await page.waitForSelector(".loginGuestBtn");
      assertEq(await page.getByRole("button", { name: "Try the demo" }).count(), 0, "no demo button off a demo server");
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

  await step("auth: demo mode leads with Try the demo, folds the sign-in and offers the first tour", async () => {
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
      await page.getByRole("button", { name: "Show me", exact: true }).click();
      await page.locator('[data-guide-overlay="welcome"] .guideCard').waitFor();
      await page.keyboard.press("Escape");
      await page.locator('[data-guide-overlay]').waitFor({ state: "detached" });
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await admin.api("/api/admin/settings", { method: "PUT", body: { demo_mode: false } });
    }
  });
}
